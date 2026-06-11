"""Benchmark-validation gate for authored tools.

A candidate tool is trusted only when BOTH checks pass:

1. A focused per-tool acceptance brief: the authored script actually runs
   in the sandbox over a representative probe input and returns structured
   output (proves the tool works before it is ever trusted).
2. A golden-company scorecard delta: registering the candidate must not
   regress the golden benchmark (``candidate_total >= baseline_total +
   min_score_margin``), so a tool that helps in isolation but harms the
   org as a whole is rejected.

The expensive golden run is gated behind the cheap brief: it only runs
when the brief passes and ``require_golden_delta`` is set.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Final

from pydantic import JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.errors import ToolsmithError
from synthorg.meta.toolsmith.models import ToolBlueprint, ToolValidationResult
from synthorg.meta.toolsmith.protocol import (
    GoldenScorecardProvider,
    ToolAcceptanceBriefRunner,
)
from synthorg.meta.toolsmith.script_handler import run_dynamic_tool_probe
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_BRIEF_PARSE_FAILED,
    TOOLSMITH_VALIDATION_FAILED,
    TOOLSMITH_VALIDATION_PASSED,
    TOOLSMITH_VALIDATION_STARTED,
)
from synthorg.tools.sandbox.protocol import SandboxBackend

type SandboxResolver = Callable[[ToolBlueprint], SandboxBackend]

logger = get_logger(__name__)

_BRIEF_PASS_SCORE: Final[int] = 100
_BRIEF_FAIL_SCORE: Final[int] = 0
_DEFAULT_BRIEF_TIMEOUT_SECONDS: Final[float] = 30.0

_PROBE_VALUES: Mapping[str, object] = {
    "string": "probe",
    "integer": 1,
    "number": 1.0,
    "boolean": True,
    "array": [],
    "object": {},
}


def _synthesize_probe(parameters_schema: dict[str, JsonValue]) -> dict[str, object]:
    """Build a minimal valid argument payload from required schema fields.

    Honours the schema keywords that fully determine a valid value --
    ``const``, ``default``, and ``enum`` (first allowed) -- in that
    priority order, falling back to a type-based placeholder. Deeper
    constraint satisfaction (numeric bounds, ``minLength``, recursive
    container shapes) is intentionally out of scope: the probe is a
    best-effort smoke test and a probe that misses a bound merely fails
    the brief gracefully via ``_BRIEF_FAIL_SCORE`` rather than crashing.

    Returns:
        Mapping with the declared key/value types.
    """
    properties = parameters_schema.get("properties")
    required = parameters_schema.get("required") or ()
    probe: dict[str, object] = {}
    if not isinstance(properties, dict) or not isinstance(required, (list, tuple)):
        return probe
    for name in required:
        if not isinstance(name, str):
            continue
        prop = properties.get(name)
        # deepcopy so a script that mutates list/dict probes cannot leak
        # state into a subsequent invocation reusing the same singleton.
        probe[name] = deepcopy(_probe_value_for(prop))
    return probe


def _probe_value_for(prop: object) -> object:
    """Pick a probe value for a single property schema.

    ``const`` / ``default`` / ``enum`` pin an exact valid value when
    present; otherwise fall back to a type-based placeholder.

    Returns:
        ``Any`` instance.
    """
    if not isinstance(prop, dict):
        return "probe"
    if "const" in prop:
        return prop["const"]
    if "default" in prop:
        return prop["default"]
    enum = prop.get("enum")
    if isinstance(enum, (list, tuple)) and enum:
        return enum[0]
    raw_type = prop.get("type")
    json_type = raw_type if isinstance(raw_type, str) else ""
    return _PROBE_VALUES.get(json_type, "probe")


class SandboxBriefRunner:
    """Runs an authored tool in the sandbox against a synthesized probe.

    Passes iff the tool exits cleanly and returns a structured (``ok``)
    envelope for a representative input. The sandbox backend is resolved
    per blueprint so a Docker-declared tool is probed under Docker, not a
    weaker default.

    Args:
        sandbox_resolver: Resolves the sandbox backend for a blueprint.
        timeout_seconds: Per-probe wall-clock budget.
    """

    def __init__(
        self,
        sandbox_resolver: SandboxResolver,
        *,
        timeout_seconds: float = _DEFAULT_BRIEF_TIMEOUT_SECONDS,
    ) -> None:
        self._sandbox_resolver = sandbox_resolver
        self._timeout_seconds = timeout_seconds

    async def run(self, blueprint: ToolBlueprint) -> tuple[bool, int]:
        """Execute the acceptance probe; return ``(passed, score)``.

        Returns:
            Tuple of the declared element types.
        """
        import json as _json  # noqa: PLC0415

        probe = _synthesize_probe(blueprint.parameters_schema)
        # The brief passes iff the tool exits cleanly; a probe crash
        # (sandbox failure, runtime error) must fail the brief, not
        # propagate out of the gate. System-critical errors still escape.
        try:
            # The probe path is app-state-free: it executes ``script_body`` in
            # the sandbox and maps the result to an envelope, so the brief
            # runner never needs (and never fabricates) an ``AppState``.
            raw = await run_dynamic_tool_probe(
                blueprint,
                self._sandbox_resolver(blueprint),
                probe,
                timeout_seconds=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_BRIEF_PARSE_FAILED,
                tool_name=blueprint.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False, _BRIEF_FAIL_SCORE
        try:
            envelope = _json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.warning(
                TOOLSMITH_BRIEF_PARSE_FAILED,
                tool_name=blueprint.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False, _BRIEF_FAIL_SCORE
        # A tool that returns a JSON list/string/number is parseable but
        # not a valid envelope; reject without an AttributeError on .get.
        if not isinstance(envelope, dict):
            logger.warning(
                TOOLSMITH_BRIEF_PARSE_FAILED,
                tool_name=blueprint.name,
                error_type="EnvelopeTypeError",
                error="tool output must be a JSON object envelope",
            )
            return False, _BRIEF_FAIL_SCORE
        passed = envelope.get("status") == "ok"
        return passed, (_BRIEF_PASS_SCORE if passed else _BRIEF_FAIL_SCORE)


class BenchmarkToolValidationGate:
    """Two-stage benchmark gate: acceptance brief then golden delta.

    Args:
        config: Toolsmith configuration (validation thresholds).
        brief_runner: Runs the per-tool acceptance brief.
        scorecard_provider: Scores the golden benchmark with/without the
            candidate. Required when ``validation.require_golden_delta``.
    """

    def __init__(
        self,
        *,
        config: ToolsmithConfig,
        brief_runner: ToolAcceptanceBriefRunner,
        scorecard_provider: GoldenScorecardProvider | None = None,
    ) -> None:
        self._config = config
        self._brief_runner = brief_runner
        self._scorecard_provider = scorecard_provider

    async def validate(self, blueprint: ToolBlueprint) -> ToolValidationResult:
        """Run both validation stages and return the gate decision.

        Returns:
            ``ToolValidationResult`` instance.

        Raises:
            ToolValidationConfigError: Raised on the corresponding failure path.
        """
        logger.info(TOOLSMITH_VALIDATION_STARTED, tool_name=blueprint.name)
        brief_passed, brief_score = await self._brief_runner.run(blueprint)
        validation_cfg = self._config.validation

        baseline = 0
        candidate = 0
        if validation_cfg.require_golden_delta and brief_passed:
            if self._scorecard_provider is None:
                msg = "require_golden_delta is set but no scorecard provider is wired"
                raise ToolValidationConfigError(msg)
            baseline, candidate = await self._scorecard_provider.score(blueprint)
        margin = candidate - baseline

        if not validation_cfg.require_golden_delta:
            passed = brief_passed
            detail = f"brief={'pass' if brief_passed else 'fail'}; golden skipped"
        elif not brief_passed:
            # The golden run is gated behind a passing brief, so when the
            # brief fails the scorecard never ran. Report it as skipped
            # rather than a 0-0 scorecard so a real zero margin and a
            # never-run golden stage stay distinguishable.
            passed = False
            detail = "brief=fail; golden skipped"
        else:
            passed = margin >= validation_cfg.min_score_margin
            detail = (
                f"brief=pass; "
                f"golden baseline={baseline} candidate={candidate} "
                f"margin={margin} min={validation_cfg.min_score_margin}"
            )

        result = ToolValidationResult(
            passed=passed,
            brief_passed=brief_passed,
            brief_score=brief_score,
            baseline_score=baseline,
            candidate_score=candidate,
            margin=margin,
            detail=NotBlankStr(detail),
        )
        event = TOOLSMITH_VALIDATION_PASSED if passed else TOOLSMITH_VALIDATION_FAILED
        logger.info(event, tool_name=blueprint.name, detail=detail)
        return result


class ToolValidationConfigError(ToolsmithError):
    """Raised when the gate is misconfigured (golden delta without provider)."""

    default_message = "Tool validation gate is misconfigured"


__all__ = [
    "BenchmarkToolValidationGate",
    "SandboxBriefRunner",
    "ToolValidationConfigError",
]
