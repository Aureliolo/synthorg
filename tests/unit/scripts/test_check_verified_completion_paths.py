"""Tests for the verified-initiative-completion gate.

Each invariant is checked against a synthetic repo that violates exactly one of
them, so a gate that silently stopped enforcing something fails here rather
than at the next review.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_verified_completion_paths.py"


class _ScriptModule(Protocol):
    """Subset of the gate's surface these tests exercise."""

    @staticmethod
    def _check_state_machines(root: Path) -> list[str]: ...
    @staticmethod
    def _check_derivation_never_completes(root: Path) -> list[str]: ...
    @staticmethod
    def _check_plan_completion_writers(root: Path) -> list[str]: ...
    @staticmethod
    def _check_artifact_invariant(root: Path) -> list[str]: ...


def _load_script() -> _ScriptModule:
    """Load the gate by path, as the sibling gate tests do.

    Returns:
        The imported module, typed by the protocol above.
    """
    spec = importlib.util.spec_from_file_location(
        "_check_verified_completion_paths",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_ScriptModule, module)


_MODULE = _load_script()
_check_state_machines = _MODULE._check_state_machines
_check_derivation_never_completes = _MODULE._check_derivation_never_completes
_check_plan_completion_writers = _MODULE._check_plan_completion_writers
_check_artifact_invariant = _MODULE._check_artifact_invariant

_CLEAN_PLAN_TRANSITIONS = """
VALID_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.EXECUTING: frozenset({PlanStatus.INTEGRATING}),
    PlanStatus.INTEGRATING: frozenset({PlanStatus.EVALUATING}),
    PlanStatus.EVALUATING: frozenset({PlanStatus.COMPLETED}),
}
"""

_CLEAN_PROJECT_TRANSITIONS = """
VALID_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.ACTIVE: frozenset({ProjectStatus.INTEGRATING}),
    ProjectStatus.INTEGRATING: frozenset({ProjectStatus.EVALUATING}),
    ProjectStatus.EVALUATING: frozenset({ProjectStatus.COMPLETED}),
}
"""

_CLEAN_PLAN_TRANSITIONS_UNANNOTATED = """
VALID_TRANSITIONS = {
    PlanStatus.EXECUTING: frozenset({PlanStatus.INTEGRATING}),
    PlanStatus.INTEGRATING: frozenset({PlanStatus.EVALUATING}),
    PlanStatus.EVALUATING: frozenset({PlanStatus.COMPLETED}),
}
"""

_CLEAN_DERIVATION = """
def derive_plan_status(items, *, current):
    return PlanStatus.INTEGRATING
"""

_CLEAN_VALIDATOR = """
def _validate(self):
    validate_expected_artifacts(kind=self.kind, artifacts=self.expected_artifacts)
"""


def _write(root: Path, rel: str, body: str) -> None:
    """Write *body* to *rel* under *root*, creating parents."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a synthetic repo that satisfies every invariant.

    Returns:
        The repo root, ready for one file to be made non-compliant.
    """
    _write(
        tmp_path,
        "src/synthorg/core/plan_transitions.py",
        _CLEAN_PLAN_TRANSITIONS,
    )
    _write(
        tmp_path,
        "src/synthorg/core/project_transitions.py",
        _CLEAN_PROJECT_TRANSITIONS,
    )
    _write(
        tmp_path,
        "src/synthorg/engine/initiative/completion.py",
        _CLEAN_DERIVATION,
    )
    _write(tmp_path, "src/synthorg/core/plan.py", _CLEAN_VALIDATOR)
    _write(
        tmp_path,
        "src/synthorg/engine/decomposition/models.py",
        _CLEAN_VALIDATOR,
    )
    _write(tmp_path, "src/synthorg/engine/initiative/evaluate.py", "")
    return tmp_path


class TestStateMachines:
    """The tail cannot be skipped."""

    def test_a_clean_repo_passes(self, repo: Path) -> None:
        assert _check_state_machines(repo) == []

    def test_a_restored_executing_to_completed_edge_is_caught(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/core/plan_transitions.py",
            _CLEAN_PLAN_TRANSITIONS.replace(
                "PlanStatus.EXECUTING: frozenset({PlanStatus.INTEGRATING})",
                "PlanStatus.EXECUTING: frozenset("
                "{PlanStatus.INTEGRATING, PlanStatus.COMPLETED})",
            ),
        )

        messages = _check_state_machines(repo)

        assert any("EXECUTING -> COMPLETED is back" in m for m in messages)

    def test_a_restored_active_to_completed_edge_is_caught(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/core/project_transitions.py",
            _CLEAN_PROJECT_TRANSITIONS.replace(
                "ProjectStatus.ACTIVE: frozenset({ProjectStatus.INTEGRATING})",
                "ProjectStatus.ACTIVE: frozenset("
                "{ProjectStatus.INTEGRATING, ProjectStatus.COMPLETED})",
            ),
        )

        messages = _check_state_machines(repo)

        assert any("ACTIVE -> COMPLETED is back" in m for m in messages)

    def test_a_second_predecessor_of_completed_is_caught(self, repo: Path) -> None:
        """Delivery has exactly one predecessor, whichever one it is."""
        _write(
            repo,
            "src/synthorg/core/plan_transitions.py",
            _CLEAN_PLAN_TRANSITIONS.replace(
                "PlanStatus.INTEGRATING: frozenset({PlanStatus.EVALUATING})",
                "PlanStatus.INTEGRATING: frozenset("
                "{PlanStatus.EVALUATING, PlanStatus.COMPLETED})",
            ),
        )

        messages = _check_state_machines(repo)

        assert any("exactly one predecessor" in m for m in messages)

    def test_an_unreadable_table_is_reported_not_ignored(self, repo: Path) -> None:
        _write(repo, "src/synthorg/core/plan_transitions.py", "def broken(:\n")

        messages = _check_state_machines(repo)

        assert any("unreadable" in m for m in messages)

    def test_an_unannotated_table_is_read(self, repo: Path) -> None:
        """A bare ``VALID_TRANSITIONS = {...}`` (no annotation) still parses."""
        _write(
            repo,
            "src/synthorg/core/plan_transitions.py",
            _CLEAN_PLAN_TRANSITIONS_UNANNOTATED,
        )

        assert _check_state_machines(repo) == []


class TestDerivation:
    """The rollup's derivation cannot produce delivery."""

    def test_a_clean_derivation_passes(self, repo: Path) -> None:
        assert _check_derivation_never_completes(repo) == []

    def test_a_completed_branch_in_the_derivation_is_caught(self, repo: Path) -> None:
        """`_advance_plan(plan, derived)` carries no literal for the writer
        check to match, so this is the half that keeps it honest."""
        _write(
            repo,
            "src/synthorg/engine/initiative/completion.py",
            "def derive_plan_status(items, *, current):\n"
            "    return PlanStatus.COMPLETED\n",
        )

        messages = _check_derivation_never_completes(repo)

        assert any("second delivery path" in m for m in messages)

    def test_an_async_completed_branch_is_caught(self, repo: Path) -> None:
        """A renamed-to-async derivation is scanned like a sync one."""
        _write(
            repo,
            "src/synthorg/engine/initiative/completion.py",
            "async def derive_plan_status(items, *, current):\n"
            "    return PlanStatus.COMPLETED\n",
        )

        messages = _check_derivation_never_completes(repo)

        assert any("second delivery path" in m for m in messages)

    def test_a_missing_derivation_is_caught(self, repo: Path) -> None:
        """A removed or renamed derivation disarms the invariant, so it fails."""
        _write(
            repo,
            "src/synthorg/engine/initiative/completion.py",
            "def summarise_progress(items):\n    return items\n",
        )

        messages = _check_derivation_never_completes(repo)

        assert any("not found" in m for m in messages)


class TestCompletionWriters:
    """Only the evaluate stage writes a plan's COMPLETED status."""

    def test_a_clean_repo_passes(self, repo: Path) -> None:
        assert _check_plan_completion_writers(repo) == []

    def test_the_owner_may_write_it(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/engine/initiative/evaluate.py",
            "await writer.sync_status(plan, PlanStatus.COMPLETED)\n",
        )

        assert _check_plan_completion_writers(repo) == []

    def test_another_module_writing_it_is_caught(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/engine/initiative/sneaky.py",
            "await writer.sync_status(plan, PlanStatus.COMPLETED)\n",
        )

        messages = _check_plan_completion_writers(repo)

        assert any("sneaky.py" in m for m in messages)

    def test_a_justified_opt_out_is_honoured(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/engine/initiative/sneaky.py",
            "await writer.sync_status(plan, PlanStatus.COMPLETED)"
            "  # lint-allow: verified-completion -- a stated reason\n",
        )

        assert _check_plan_completion_writers(repo) == []

    def test_a_multiline_justified_opt_out_is_honoured(self, repo: Path) -> None:
        """The docstring sanctions a marker on the call's closing-paren line."""
        _write(
            repo,
            "src/synthorg/engine/initiative/sneaky.py",
            "await writer.sync_status(\n"
            "    plan, PlanStatus.COMPLETED\n"
            ")  # lint-allow: verified-completion -- a stated reason\n",
        )

        assert _check_plan_completion_writers(repo) == []

    def test_an_unjustified_opt_out_is_refused(self, repo: Path) -> None:
        """A bare marker with no reason is not an opt-out."""
        _write(
            repo,
            "src/synthorg/engine/initiative/sneaky.py",
            "await writer.sync_status(plan, PlanStatus.COMPLETED)"
            "  # lint-allow: verified-completion\n",
        )

        assert _check_plan_completion_writers(repo) != []


class TestArtifactInvariant:
    """Every work unit declares a deliverable."""

    def test_a_clean_repo_passes(self, repo: Path) -> None:
        assert _check_artifact_invariant(repo) == []

    @pytest.mark.parametrize(
        "rel",
        [
            "src/synthorg/core/plan.py",
            "src/synthorg/engine/decomposition/models.py",
        ],
    )
    def test_a_dropped_validator_call_is_caught(self, repo: Path, rel: str) -> None:
        _write(repo, rel, "def _validate(self):\n    return self\n")

        messages = _check_artifact_invariant(repo)

        assert any(rel in m for m in messages)
