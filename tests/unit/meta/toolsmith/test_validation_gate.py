# mypy: disable-error-code="explicit-any"
"""Unit tests for the benchmark tool-validation gate."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.config import ToolsmithConfig, ToolValidationConfig
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.validation_gate import (
    BenchmarkToolValidationGate,
    SandboxBriefRunner,
    ToolValidationConfigError,
)
from synthorg.tools.sandbox.result import SandboxResult

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="print('{}')",
        action_type="code:read",
        created_at=_NOW,
    )


class _FakeSandbox:
    def __init__(self, result: SandboxResult) -> None:
        self._result = result

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Any = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: Any = None,
        project_id: Any = None,
    ) -> SandboxResult:
        del command, args, cwd, env_overrides, timeout, owner_id, project_id
        return self._result

    async def cleanup(self) -> None:
        return None

    def get_backend_type(self) -> NotBlankStr:
        return NotBlankStr("subprocess")

    async def release_owner(
        self,
        owner_id: Any,
        *,
        project_id: Any = None,
        image_override: str | None = None,
    ) -> None:
        del owner_id, project_id, image_override

    async def health_check(self) -> bool:
        return True


class _FakeScorecard:
    def __init__(self, baseline: int, candidate: int) -> None:
        self._baseline = baseline
        self._candidate = candidate
        self.calls = 0

    async def score(self, blueprint: ToolBlueprint) -> tuple[int, int]:
        del blueprint
        self.calls += 1
        return self._baseline, self._candidate


class _FakeBrief:
    def __init__(self, *, passed: bool, score: int) -> None:
        self._passed = passed
        self._score = score

    async def run(self, blueprint: ToolBlueprint) -> tuple[bool, int]:
        del blueprint
        return self._passed, self._score


def _config(*, require_golden: bool = True, min_margin: int = 0) -> ToolsmithConfig:
    return ToolsmithConfig(
        enabled=True,
        allowed_capabilities=(NotBlankStr("textkit:slugify"),),
        validation=ToolValidationConfig(
            require_golden_delta=require_golden, min_score_margin=min_margin
        ),
    )


class TestSandboxBriefRunner:
    async def test_ok_envelope_passes(self) -> None:
        sandbox = _FakeSandbox(
            SandboxResult(stdout='{"slug": "x"}', stderr="", returncode=0)
        )
        runner = SandboxBriefRunner(lambda _bp: sandbox)  # type: ignore[arg-type,return-value]
        passed, score = await runner.run(_blueprint())
        assert passed is True
        assert score == 100

    async def test_failure_envelope_fails(self) -> None:
        sandbox = _FakeSandbox(SandboxResult(stdout="", stderr="boom", returncode=1))
        runner = SandboxBriefRunner(lambda _bp: sandbox)  # type: ignore[arg-type,return-value]
        passed, score = await runner.run(_blueprint())
        assert passed is False
        assert score == 0


class TestBenchmarkToolValidationGate:
    async def test_brief_pass_and_no_regression_passes(self) -> None:
        gate = BenchmarkToolValidationGate(
            config=_config(),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=_FakeScorecard(100, 101),
        )
        result = await gate.validate(_blueprint())
        assert result.passed is True
        assert result.margin == 1

    async def test_regression_blocks(self) -> None:
        gate = BenchmarkToolValidationGate(
            config=_config(),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=_FakeScorecard(100, 95),
        )
        result = await gate.validate(_blueprint())
        assert result.passed is False
        assert result.margin == -5

    async def test_brief_failure_skips_golden(self) -> None:
        scorecard = _FakeScorecard(100, 200)
        gate = BenchmarkToolValidationGate(
            config=_config(),
            brief_runner=_FakeBrief(passed=False, score=0),
            scorecard_provider=scorecard,
        )
        result = await gate.validate(_blueprint())
        assert result.passed is False
        assert scorecard.calls == 0  # golden gated behind the brief

    async def test_min_margin_enforced(self) -> None:
        gate = BenchmarkToolValidationGate(
            config=_config(min_margin=5),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=_FakeScorecard(100, 103),
        )
        result = await gate.validate(_blueprint())
        assert result.passed is False  # margin 3 < min 5
        assert result.margin == 3

    async def test_golden_disabled_uses_brief_only(self) -> None:
        gate = BenchmarkToolValidationGate(
            config=_config(require_golden=False),
            brief_runner=_FakeBrief(passed=True, score=100),
        )
        result = await gate.validate(_blueprint())
        assert result.passed is True
        assert result.baseline_score == 0
        assert result.candidate_score == 0

    async def test_golden_required_without_provider_raises(self) -> None:
        gate = BenchmarkToolValidationGate(
            config=_config(),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=None,
        )
        with pytest.raises(ToolValidationConfigError):
            await gate.validate(_blueprint())
