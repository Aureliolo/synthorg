"""Unit tests for the pre-run artifact baseline.

The baseline is what turns "these paths exist" into "this run delivered".
Its degradation path therefore decides whether a run that edited a file is
read as having produced nothing, so what each ``None`` means, and which
failures reach the caller, are the cases that matter.
"""

from collections.abc import Sequence

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.baseline_scope import (
    artifact_baseline_scope,
    capture_run_baseline,
    current_artifact_baseline,
)
from synthorg.engine.artifacts.expected_artifact_check import ArtifactPresence

pytestmark = pytest.mark.unit

_EXPECTED = (ExpectedArtifact(type=ArtifactType.CODE, path=NotBlankStr("src/a.py")),)
_ANSWER = ArtifactPresence(
    probed=("src/a.py",), missing=(), digests={"src/a.py": "abc123"}
)


async def _answering(
    _project_id: str, _expected: Sequence[ExpectedArtifact]
) -> ArtifactPresence:
    """Return a fixed presence, as a wired probe would.

    Returns:
        The canned answer.
    """
    return _ANSWER


class TestCaptureRunBaseline:
    async def test_a_wired_probe_supplies_the_baseline(self) -> None:
        captured = await capture_run_baseline(
            _answering, project_id="proj-1", expected=_EXPECTED
        )

        assert captured == _ANSWER

    async def test_nothing_declared_asks_nothing(self) -> None:
        """A task that declared no artifacts has no baseline to want."""

        async def _never_called(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> ArtifactPresence:
            msg = "probed a task that declared nothing"
            raise AssertionError(msg)

        assert (
            await capture_run_baseline(_never_called, project_id="proj-1", expected=())
            is None
        )

    async def test_an_unwired_probe_degrades_to_presence(self) -> None:
        assert (
            await capture_run_baseline(None, project_id="proj-1", expected=_EXPECTED)
            is None
        )

    async def test_an_unreadable_workspace_degrades_to_presence(self) -> None:
        """Storage faults must not fail a run that delivered."""

        async def _refusing(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> ArtifactPresence:
            msg = "workspace is not readable"
            raise PermissionError(msg)

        assert (
            await capture_run_baseline(
                _refusing, project_id="proj-1", expected=_EXPECTED
            )
            is None
        )

    async def test_a_probe_bug_reaches_the_caller(self) -> None:
        """The post-run half catches ``OSError`` alone, so this half must too.

        Swallowing a programming error here while the same error crashes the
        post-run probe would report one bug two incompatible ways: a silently
        disabled baseline on one side, a failed run on the other.
        """

        async def _broken(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> ArtifactPresence:
            msg = "probe called with the wrong shape"
            raise TypeError(msg)

        with pytest.raises(TypeError):
            await capture_run_baseline(_broken, project_id="proj-1", expected=_EXPECTED)


class TestArtifactBaselineScope:
    def test_the_scope_publishes_and_restores(self) -> None:
        assert current_artifact_baseline() is None
        with artifact_baseline_scope(_ANSWER):
            assert current_artifact_baseline() == _ANSWER
        assert current_artifact_baseline() is None

    def test_a_nested_scope_restores_the_outer_baseline(self) -> None:
        """Recovery retries nest inside the original run's scope."""
        outer = ArtifactPresence(probed=("src/a.py",), missing=("src/a.py",))
        with artifact_baseline_scope(outer):
            with artifact_baseline_scope(_ANSWER):
                assert current_artifact_baseline() == _ANSWER
            assert current_artifact_baseline() == outer
