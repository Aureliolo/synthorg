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
    @staticmethod
    def _check_post_execution_guards(root: Path) -> list[str]: ...
    @staticmethod
    def _check_test_evidence_provenance(root: Path) -> list[str]: ...


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
_check_post_execution_guards = _MODULE._check_post_execution_guards
_check_test_evidence_provenance = _MODULE._check_test_evidence_provenance

_CLEAN_TEST_CAPTURE = """
def record_if_test_run(result, *, command, records, clock):
    if not classify(command):
        return
    records.append(record(purpose=CodeExecutionPurpose.TESTS))
"""

_CLEAN_MODEL_FACING_TOOL = """
class Tool:
    async def execute(self, *, arguments):
        await record_if_test_run(result, command=arguments["command"])
"""

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

_CLEAN_POST_EXECUTION = """
_UNFINISHED_REASONS = {
    TerminationReason.MAX_TURNS: "turn cap",
    TerminationReason.BUDGET_EXHAUSTED: "budget",
    TerminationReason.STAGNATION: "stagnation",
}


async def apply_post_execution_transitions(result, *, artifact_probe=None):
    absent = _absent_artifacts(artifact_probe, result.context)
    return absent
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
    _write(tmp_path, "src/synthorg/engine/task_sync.py", _CLEAN_POST_EXECUTION)
    _write(
        tmp_path,
        "src/synthorg/tools/_test_run_capture.py",
        _CLEAN_TEST_CAPTURE,
    )
    for rel in ("code_runner.py", "terminal/shell_command.py"):
        _write(tmp_path, f"src/synthorg/tools/{rel}", _CLEAN_MODEL_FACING_TOOL)
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


class TestPostExecutionGuards:
    """A run that did not deliver never reads as one that did."""

    def test_a_clean_repo_passes(self, repo: Path) -> None:
        assert _check_post_execution_guards(repo) == []

    def test_a_dropped_artifact_probe_is_caught(self, repo: Path) -> None:
        """Without the probe the zero-tool-call proxy is the only signal."""
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            _CLEAN_POST_EXECUTION.replace(
                "absent = _absent_artifacts(artifact_probe, result.context)",
                "absent = ()",
            ),
        )

        messages = _check_post_execution_guards(repo)

        assert any("_absent_artifacts" in m for m in messages)

    def test_a_dropped_reason_table_is_caught(self, repo: Path) -> None:
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            "async def apply_post_execution_transitions(result, *, "
            "artifact_probe=None):\n"
            "    return _absent_artifacts(artifact_probe, result.context)\n",
        )

        messages = _check_post_execution_guards(repo)

        assert any("_UNFINISHED_REASONS" in m for m in messages)

    @pytest.mark.parametrize("reason", ["MAX_TURNS", "BUDGET_EXHAUSTED", "STAGNATION"])
    def test_a_reason_dropped_from_the_table_is_caught(
        self, repo: Path, reason: str
    ) -> None:
        """Each unfinished reason needs its own terminal status.

        Dropping one leaves exactly that run sitting at IN_PROGRESS, which
        the stall derivation reads as still moving.
        """
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            _CLEAN_POST_EXECUTION.replace(f"TerminationReason.{reason}", "_removed"),
        )

        messages = _check_post_execution_guards(repo)

        assert any(reason in m for m in messages)

    def test_a_probe_stranded_in_an_unreached_helper_is_caught(
        self, repo: Path
    ) -> None:
        """Present in the module is not the same as reached by the entry point.

        This is the shape a refactor produces by accident: the probe still
        exists, still type-checks, and nothing calls it. A module-wide name
        match reads that as guarded.
        """
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            _CLEAN_POST_EXECUTION.replace(
                "absent = _absent_artifacts(artifact_probe, result.context)",
                "absent = ()",
            )
            + "\n\nasync def _orphan(artifact_probe, ctx):\n"
            "    return _absent_artifacts(artifact_probe, ctx)\n",
        )

        messages = _check_post_execution_guards(repo)

        assert any("_absent_artifacts" in m for m in messages)

    def test_a_probe_moved_into_a_called_helper_still_passes(self, repo: Path) -> None:
        """The honest refactor is not a regression: the entry point reaches it."""
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            _CLEAN_POST_EXECUTION.replace(
                "absent = _absent_artifacts(artifact_probe, result.context)",
                "absent = await _undelivered(artifact_probe, result.context)",
            )
            + "\n\nasync def _undelivered(artifact_probe, ctx):\n"
            "    return _absent_artifacts(artifact_probe, ctx)\n",
        )

        assert _check_post_execution_guards(repo) == []

    def test_an_empty_table_is_caught_despite_the_names_appearing(
        self, repo: Path
    ) -> None:
        """The reasons must be IN the table, not merely somewhere in the file.

        A text search passes on a module whose table is empty while the
        member names survive in a comment or an unrelated branch, which
        terminalises nothing.
        """
        emptied = _CLEAN_POST_EXECUTION[
            : _CLEAN_POST_EXECUTION.index("_UNFINISHED_REASONS")
        ]
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            emptied + "_UNFINISHED_REASONS = {}\n\n"
            "# TerminationReason.MAX_TURNS TerminationReason.BUDGET_EXHAUSTED\n"
            "# TerminationReason.STAGNATION\n"
            "async def apply_post_execution_transitions(result, *, "
            "artifact_probe=None):\n"
            "    return _absent_artifacts(artifact_probe, result.context)\n",
        )

        messages = _check_post_execution_guards(repo)

        assert len(messages) == 3

    def test_reasons_only_on_the_value_side_terminalise_nothing(
        self, repo: Path
    ) -> None:
        """A reason in the message is not an entry keyed by that reason.

        The table maps each reason to the text a task carries when it ends
        there. Reading the whole assignment would accept a table keyed by
        something else that merely names the reasons in its messages, which
        leaves every one of those runs at IN_PROGRESS.
        """
        emptied = _CLEAN_POST_EXECUTION[
            : _CLEAN_POST_EXECUTION.index("_UNFINISHED_REASONS")
        ]
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            emptied + "_UNFINISHED_REASONS = {\n"
            '    "turn cap": TerminationReason.MAX_TURNS,\n'
            '    "budget": TerminationReason.BUDGET_EXHAUSTED,\n'
            '    "stagnation": TerminationReason.STAGNATION,\n'
            "}\n\n\n"
            "async def apply_post_execution_transitions(result, *, "
            "artifact_probe=None):\n"
            "    return _absent_artifacts(artifact_probe, result.context)\n",
        )

        messages = _check_post_execution_guards(repo)

        assert len(messages) == 3

    def test_a_missing_entry_point_is_caught(self, repo: Path) -> None:
        """Nothing applies the guards if the function they live in is gone."""
        _write(
            repo,
            "src/synthorg/engine/task_sync.py",
            _CLEAN_POST_EXECUTION.replace(
                "async def apply_post_execution_transitions", "async def _renamed"
            ),
        )

        messages = _check_post_execution_guards(repo)

        assert any("apply_post_execution_transitions" in m for m in messages)

    def test_an_unreadable_module_is_reported_not_ignored(self, repo: Path) -> None:
        _write(repo, "src/synthorg/engine/task_sync.py", "def broken(:\n")

        assert _check_post_execution_guards(repo) != []


class TestTestEvidenceProvenance:
    """What arms the build/test oracle is the command, never a label."""

    def test_a_clean_repo_passes(self, repo: Path) -> None:
        assert _check_test_evidence_provenance(repo) == []

    def test_a_model_facing_purpose_argument_is_caught(self, repo: Path) -> None:
        """A purpose the agent supplies is a claim, not evidence."""
        _write(
            repo,
            "src/synthorg/tools/code_runner.py",
            "class Tool:\n"
            "    async def execute(self, *, arguments, purpose='general'):\n"
            "        return purpose\n",
        )

        messages = _check_test_evidence_provenance(repo)

        assert any("purpose" in m for m in messages)

    def test_a_purpose_field_on_the_args_model_is_caught(self, repo: Path) -> None:
        """A declared field is the same hand-back as a parameter.

        The tool's args model is what the model fills in, so a ``purpose``
        field there is exactly the label the oracle must not read.
        """
        _write(
            repo,
            "src/synthorg/tools/code_runner.py",
            "class Args(BaseModel):\n    purpose: str = Field(default='general')\n",
        )

        messages = _check_test_evidence_provenance(repo)

        assert any("purpose" in m for m in messages)

    def test_forwarding_a_purpose_onward_is_not_a_declaration(self, repo: Path) -> None:
        """The module deciding a purpose is the opposite of the model doing it.

        A keyword the tool passes on to something it calls is its own
        decision, so flagging it would make the gate fire on the honest
        shape and teach the next reader to work around it.
        """
        _write(
            repo,
            "src/synthorg/tools/code_runner.py",
            "class Tool:\n"
            "    async def execute(self, *, arguments):\n"
            "        return record(command, purpose=classify(command))\n",
        )

        assert _check_test_evidence_provenance(repo) == []

    def test_a_second_source_of_test_evidence_is_caught(self, repo: Path) -> None:
        """One door: a tool stamping TESTS itself bypasses classification."""
        _write(
            repo,
            "src/synthorg/tools/terminal/shell_command.py",
            "class Tool:\n"
            "    async def execute(self, *, arguments):\n"
            "        return record(CodeExecutionPurpose.TESTS)\n",
        )

        messages = _check_test_evidence_provenance(repo)

        assert any("second source" in m for m in messages)

    def test_an_owner_that_stopped_minting_is_caught(self, repo: Path) -> None:
        """No mint means the oracle abstains on every task, silently."""
        _write(
            repo,
            "src/synthorg/tools/_test_run_capture.py",
            "def record_if_test_run(result, *, command, records, clock):\n"
            "    return None\n",
        )

        messages = _check_test_evidence_provenance(repo)

        assert any("no longer stamps" in m for m in messages)
