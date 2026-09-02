# module-kind: code
"""Read each treatment off the wire before a matrix is paid for.

A 200 response, a valid manifest and a green unit test are all compatible
with the treatment being absent, and every layer above the engine stays
green throughout, because a config that PARSES is not one that APPLIES and
nothing asks the difference unless something is built to.

So one cell is run first, and what it ran under is read from the evidence
rather than the configuration: the engine's own wiring summary (what was
bound, and the tool surface the invoker was built with), the live settings
the manifest was armed into, the ledger the cell's spend landed in, and the
request bodies the transcript tap recorded. Each treatment becomes a finding
with what was expected and what was seen, and a recording refuses to start
without a passing set for its own manifest digest. The findings travel in the
report, so a published artefact states its wiring rather than asserting it.

A treatment whose evidence cannot be read is UNVERIFIED, which is neither a
pass nor a failure and is said in those words: a provider that publishes no
cache figures has not failed the caching check, and a smoke that found no
request to read has not passed the reasoning one.
"""

import asyncio
import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from evals.errors import RecursionDepthSmokeRequiredError
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth.manifest import Arm, ModelPair, RecursionDepthManifest
from evals.recursion_depth.models import WiringFinding, WiringReport
from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.completion_enums import REASONING_UNSET, ReasoningEffort
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.wiring_summary import EngineWiringSummary
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_SMOKE_UNVERIFIED,
    EVALS_RECURSION_WIRING_CHECKED,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)

#: Where the smoke's own journal, report and findings go, under the out dir
#: the recording it gates will use. Apart from the recording's journal, since
#: the two are different matrices and a journal refuses a second one.
SMOKE_DIR_NAME: Final[str] = "smoke"

#: The findings file the recording reads back.
WIRING_REPORT_NAME: Final[str] = "wiring.json"

#: The strategy value under which no detector is the correct wiring.
_STAGNATION_OFF: Final[str] = "off"

#: The setting the manifest's compaction threshold was armed into.
_COMPACTION_THRESHOLD_KEY: Final[tuple[str, str]] = (
    "engine",
    "compaction_fill_threshold_percent",
)

#: The request-body field the reasoning depth travels in.
_REASONING_FIELD: Final[str] = "reasoning_effort"


def smoke_dir(out_dir: Path) -> Path:
    """Where the smoke for a recording under *out_dir* lives.

    Returns:
        The directory.
    """
    return out_dir / SMOKE_DIR_NAME


def matrix_digest(manifest: RecursionDepthManifest) -> str:
    """Hash the matrix a run EFFECTIVELY records, overrides applied.

    The manifest file's own digest is what the journal pins, and it is the
    wrong key here: every command-line override (``--depths``,
    ``--leaf-reasoning-effort``, ``--contract-stage``, ...) changes the
    treatment without touching the file, so a smoke keyed on the file would
    vouch for a recording running a treatment it never read off the wire.
    Hashed over the loaded model's canonical dump, so two invocations that
    narrow to the same matrix agree whatever flags spelled it.

    Returns:
        A ``sha256:``-prefixed digest of the narrowed matrix.
    """
    canonical = manifest.model_dump_json(exclude_none=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def smoke_manifest(manifest: RecursionDepthManifest) -> RecursionDepthManifest:
    """Narrow *manifest* to the one cell the smoke runs.

    The shallowest cap, one repetition, the gated arm where the matrix has
    one (it exercises the reviewer path the ungated arm never reaches). One
    repetition on purpose: the repetition floor is a statement about what a
    CURVE can carry, and a smoke measures wiring rather than a curve.

    Returns:
        The one-cell matrix.
    """
    depth = min(manifest.depths)
    arm = Arm.GATED if Arm.GATED in manifest.arms else manifest.arms[0]
    return manifest.model_copy(
        update={"depths": (depth,), "repetitions": {depth: 1}, "arms": (arm,)}
    )


class WiringProbe:
    """Collects what the smoke's engine was built with, then reads the evidence.

    Observes the FIRST engine the cell builds and keeps it: the tool surface is
    final only once that engine has run, so the summary is read at report time
    rather than at observation.
    """

    def __init__(self, manifest: RecursionDepthManifest) -> None:
        self._manifest = manifest
        self._engine: AgentEngine | None = None
        self._ledger: ProgressTrackingLedger | None = None

    def observe(self, engine: AgentEngine, ledger: ProgressTrackingLedger) -> None:
        """Remember the first engine built and the ledger it spends into."""
        if self._engine is None:
            self._engine = engine
            self._ledger = ledger

    async def report(
        self,
        app_state: AppState,
        *,
        transcript_root: Path | None,
        manifest_sha256: str,
    ) -> WiringReport:
        """Read every treatment off the evidence the cell left behind.

        Args:
            app_state: The live application the cell ran against.
            transcript_root: Where the tap wrote the cell's request bodies.
            manifest_sha256: The effective matrix this smoke was run for,
                as :func:`matrix_digest` spells it.

        Returns:
            The findings.

        Raises:
            RecursionDepthSmokeRequiredError: The cell built no engine, so
                there is nothing to read.
        """
        if self._engine is None or self._ledger is None:
            msg = "the smoke built no engine, so no treatment could be read"
            raise RecursionDepthSmokeRequiredError(msg)
        wiring = self._engine.wiring
        findings = (
            tool_surface_finding(wiring),
            stagnation_finding(wiring, self._manifest),
            await compaction_finding(wiring, app_state, self._manifest),
            memory_finding(app_state, self._manifest),
            budget_finding(wiring, self._ledger),
            *governance_findings(wiring),
            # Off the loop: it walks and reads every transcript the cell
            # wrote, on the same loop the gateway is still serving.
            await asyncio.to_thread(reasoning_finding, transcript_root, self._manifest),
            caching_finding(await collect_all_records(self._ledger)),
        )
        report = WiringReport(
            manifest_sha256=NotBlankStr(manifest_sha256),
            checked_at=datetime.now(UTC),
            findings=findings,
        )
        logger.info(
            EVALS_RECURSION_WIRING_CHECKED,
            passed=report.passed,
            failed=[f.treatment for f in findings if f.passed is False],
            unverified=list(report.unverified),
        )
        return report


def tool_surface_finding(wiring: EngineWiringSummary) -> WiringFinding:
    """What the invoker offered, read where the surface became final.

    Returns:
        The finding.
    """
    names = wiring.tool_surface
    if names is None:
        observed = "no invoker was built, so no surface was recorded"
    else:
        observed = f"{len(names)} tools: {', '.join(names)}"
    return WiringFinding(
        treatment=NotBlankStr("tool surface"),
        expected="a non-empty surface recorded where the invoker was built",
        observed=observed,
        passed=bool(names),
    )


def stagnation_finding(
    wiring: EngineWiringSummary, manifest: RecursionDepthManifest
) -> WiringFinding:
    """Whether the declared detector is the one watching the loop.

    Returns:
        The finding.
    """
    expected = manifest.stagnation.strategy
    observed = wiring.stagnation_strategy or "no detector"
    passed = (
        wiring.stagnation_strategy is None
        if expected == _STAGNATION_OFF
        else wiring.stagnation_strategy == expected
    )
    return WiringFinding(
        treatment=NotBlankStr("stagnation"),
        expected=expected,
        observed=observed,
        passed=passed,
    )


async def compaction_finding(
    wiring: EngineWiringSummary,
    app_state: AppState,
    manifest: RecursionDepthManifest,
) -> WiringFinding:
    """Whether context is compacted, at the threshold the manifest armed.

    The threshold is read back through the same live settings the engine
    reads it from, not off the manifest that wrote it.

    Returns:
        The finding.
    """
    declared = manifest.compaction.fill_threshold_percent
    namespace, key = _COMPACTION_THRESHOLD_KEY
    live = await settings_service_of(app_state).get(namespace, key)
    live_value = str(live.value)
    try:
        threshold_matches = float(live_value) == declared
    except ValueError:
        threshold_matches = False
    wired = "wired" if wiring.has_compaction_callback else "absent"
    return WiringFinding(
        treatment=NotBlankStr("compaction"),
        expected=f"callback wired, fill threshold {declared}%",
        observed=f"callback {wired}, live threshold {live_value}",
        passed=wiring.has_compaction_callback and threshold_matches,
    )


def memory_finding(
    app_state: AppState, manifest: RecursionDepthManifest
) -> WiringFinding:
    """Whether memory is up, on the embedder the manifest declared.

    Returns:
        The finding.
    """
    declared = manifest.embedder
    expected = serialize_model_ref(
        ModelRef(provider=declared.provider, model_id=declared.model_id)
    )
    memory = app_state.slice(MemoryStateSlice)
    if memory.backend is None:
        failure = memory.wiring_failure or "no reason recorded"
        observed = f"no backend ({failure})"
    else:
        observed = (
            f"{type(memory.backend).__name__} on {memory.embedder_ref or 'unset'}"
        )
    return WiringFinding(
        treatment=NotBlankStr("memory"),
        expected=f"a backend on {expected}",
        observed=observed,
        passed=memory.backend is not None and memory.embedder_ref == expected,
    )


def budget_finding(
    wiring: EngineWiringSummary, ledger: ProgressTrackingLedger
) -> WiringFinding:
    """Whether spend is bounded, and bounded on the ledger the cell reads.

    Identity rather than equality: an enforcer watching a lookalike tracker
    bounds nothing the cell records.

    Returns:
        The finding.
    """
    same_ledger = wiring.cost_tracker is ledger
    enforcer = "present" if wiring.has_budget_enforcer else "absent"
    tracker = "the cell ledger" if same_ledger else "another tracker"
    return WiringFinding(
        treatment=NotBlankStr("budget"),
        expected="an enforcer whose tracker IS the cell ledger",
        observed=f"enforcer {enforcer}, recording into {tracker}",
        passed=wiring.has_budget_enforcer and same_ledger,
    )


def governance_findings(wiring: EngineWiringSummary) -> tuple[WiringFinding, ...]:
    """The three governance seams a measured engine can silently run without.

    Returns:
        One finding each for review, approval and policy.
    """
    seams = (
        ("review pipeline", wiring.has_review_pipeline),
        ("approval gate", wiring.has_approval_gate),
        ("policy engine", wiring.has_policy_engine),
    )
    return tuple(
        WiringFinding(
            treatment=NotBlankStr(name),
            expected="present",
            observed="present" if present else "absent",
            passed=present,
        )
        for name, present in seams
    )


def declared_effort(pair: ModelPair) -> str | None:
    """The reasoning depth a pair declares, as the wire spells it.

    Returns:
        The value the request carries, or ``None`` when the pair sends none.
    """
    value = pair.reasoning_effort
    if value is None or value == REASONING_UNSET:
        return None
    return value.value if isinstance(value, ReasoningEffort) else str(value)


def reasoning_finding(
    transcript_root: Path | None, manifest: RecursionDepthManifest
) -> WiringFinding:
    """Whether the executor's requests carried the declared reasoning depth.

    Read off the recorded request bodies, which is the only place the value
    is known to have reached the provider. Every request for the executor's
    model must carry one of the depths the matrix declares (its own, or the
    shallow leaf pool's); a request carrying none, or another, fails.

    Returns:
        The finding, unverified when no request could be read.
    """
    executor = manifest.executor
    admissible = {declared_effort(executor)}
    if manifest.leaf_reasoning_effort is not None:
        admissible.add(manifest.leaf_reasoning_effort.value)
    expected = ", ".join(sorted(v if v is not None else "absent" for v in admissible))
    if transcript_root is None or not transcript_root.is_dir():
        return WiringFinding(
            treatment=NotBlankStr("reasoning effort"),
            expected=expected,
            observed="no transcripts were recorded",
            passed=None,
        )
    seen, unparseable = _efforts_seen(transcript_root, model_id=executor.model_id)
    if not seen:
        return WiringFinding(
            treatment=NotBlankStr("reasoning effort"),
            expected=expected,
            observed=(
                f"no request for {executor.model_id} could be read "
                f"({unparseable} unparseable lines)"
            ),
            passed=None,
        )
    counts = ", ".join(
        f"{value if value is not None else 'absent'} x{count}"
        for value, count in sorted(seen.items(), key=lambda item: str(item[0]))
    )
    return WiringFinding(
        treatment=NotBlankStr("reasoning effort"),
        expected=expected,
        observed=f"{counts} ({unparseable} unparseable lines)",
        passed=all(value in admissible for value in seen),
    )


def _efforts_seen(
    transcript_root: Path, *, model_id: str
) -> tuple[dict[str | None, int], int]:
    """Count the reasoning depth each recorded request for *model_id* carried.

    Tolerant of a line that does not parse, because the tap interleaves under
    concurrency; the count of those is reported rather than hidden.

    Returns:
        The counts per value (``None`` for absent), and the unparseable count.
    """
    seen: dict[str | None, int] = {}
    unparseable = 0
    for path in sorted(transcript_root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            request = _request_of(line)
            if request is None:
                unparseable += 1
                continue
            if request.get("model") != model_id:
                continue
            value = request.get(_REASONING_FIELD)
            key = str(value) if value is not None else None
            seen[key] = seen.get(key, 0) + 1
    return seen, unparseable


def _request_of(line: str) -> dict[str, object] | None:
    """The request body one transcript line carries, or ``None``.

    Returns:
        The body as a mapping, or ``None`` when the line or its body does not
        parse to one.
    """
    if not line.strip():
        return None
    try:
        entry = json.loads(line)
        request = entry.get("request") if isinstance(entry, dict) else None
        if isinstance(request, str):
            request = json.loads(request)
    except json.JSONDecodeError, AttributeError:
        return None
    if not isinstance(request, dict):
        return None
    return {str(key): value for key, value in request.items()}


def caching_finding(records: Sequence[CostRecord]) -> WiringFinding:
    """Whether the provider served a cached prefix on this cell's calls.

    Never a failure: a provider that publishes no cache figures reads as
    zero on every call, exactly as one that never hit would, and the two
    cannot be told apart from here.

    Returns:
        The finding.
    """
    cached = [record for record in records if record.cache_read_input_tokens > 0]
    if not records:
        observed, passed = "no call was recorded", None
    elif cached:
        observed = f"{len(cached)} of {len(records)} calls read a cached prefix"
        passed = True
    else:
        observed = (
            f"none of {len(records)} calls reported cached tokens; the provider "
            f"may not publish them"
        )
        passed = None
    return WiringFinding(
        treatment=NotBlankStr("prompt caching"),
        expected="a cached prefix read on at least one call after the first",
        observed=observed,
        passed=passed,
    )


def write_wiring_report(report: WiringReport, out_dir: Path) -> Path:
    """Write the findings beside the smoke's journal.

    Returns:
        The written path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / WIRING_REPORT_NAME
    path.write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline=""
    )
    return path


def load_wiring_report(out_dir: Path) -> WiringReport | None:
    """Read the findings a smoke left under *out_dir*, if any.

    Returns:
        The report, or ``None`` when no smoke was recorded there.
    """
    path = out_dir / WIRING_REPORT_NAME
    if not path.is_file():
        return None
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(decoded, dict):
        # Serialised as a computed field; the model derives it on read.
        decoded.pop("passed", None)
    return WiringReport.model_validate(decoded)


def require_passing_smoke(out_dir: Path, *, manifest_sha256: str) -> WiringReport:
    """Refuse a recording that cannot show a passing smoke for its matrix.

    Args:
        out_dir: The recording's output directory.
        manifest_sha256: The effective matrix the recording is about to run,
            as :func:`matrix_digest` spells it, so an override that changes
            the treatment needs its own smoke as much as an edit does.

    Returns:
        The smoke's findings, to travel in the recording's report.

    Raises:
        RecursionDepthSmokeRequiredError: No smoke, a smoke for another
            matrix, or a smoke that failed.
    """
    where = smoke_dir(out_dir)
    report = load_wiring_report(where)
    if report is None:
        msg = (
            f"no wire-level smoke is recorded under {where}; run --smoke against "
            f"this manifest and out-dir first, so the matrix is paid for on an "
            f"engine shown to carry every treatment it claims"
        )
        raise RecursionDepthSmokeRequiredError(msg)
    if report.manifest_sha256 != manifest_sha256:
        msg = (
            f"the smoke under {where} was run for manifest "
            f"{report.manifest_sha256} and this recording is for "
            f"{manifest_sha256}; a changed matrix needs its own smoke"
        )
        raise RecursionDepthSmokeRequiredError(msg)
    if not report.passed:
        failed = ", ".join(
            finding.treatment for finding in report.findings if finding.passed is False
        )
        msg = (
            f"the smoke under {where} failed on: {failed}; fix the wiring and "
            f"run --smoke again before recording"
        )
        raise RecursionDepthSmokeRequiredError(msg)
    if report.unverified:
        # Not a refusal: a flat-rate connection publishes no cache figures
        # and never will, and the report already carries the gap. What it
        # must not be is silent at the one moment the operator can still
        # stop.
        logger.warning(
            EVALS_RECURSION_SMOKE_UNVERIFIED,
            treatments=list(report.unverified),
            smoke_dir=str(where),
        )
    return report


def describe_findings(findings: Iterable[WiringFinding]) -> str:
    """Render the findings for the terminal the smoke was run from.

    Returns:
        One line per finding.
    """
    verdicts = {True: "ok        ", False: "FAILED    ", None: "unverified"}
    return "\n".join(
        f"{verdicts[finding.passed]} {finding.treatment}: "
        f"expected {finding.expected}; observed {finding.observed}"
        for finding in findings
    )


__all__ = [
    "SMOKE_DIR_NAME",
    "WIRING_REPORT_NAME",
    "WiringProbe",
    "budget_finding",
    "caching_finding",
    "compaction_finding",
    "describe_findings",
    "governance_findings",
    "load_wiring_report",
    "matrix_digest",
    "memory_finding",
    "reasoning_finding",
    "require_passing_smoke",
    "smoke_dir",
    "smoke_manifest",
    "stagnation_finding",
    "tool_surface_finding",
    "write_wiring_report",
]
