"""Recognising a run of a gate command the project's own manifest declares.

A test runner is known from its program; how a project lints, formats or checks
its dependencies is the project's decision, so it is recognised from the
declaration instead.
"""

from pathlib import Path

import pytest

from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose
from synthorg.tools._declared_gate_runs import declared_gate_purposes

pytestmark = pytest.mark.unit

_PROJECT = "proj-1"

_MANIFEST = """\
language: python
test_command: pytest
lint_command: ruff check .
format_command: ruff format --check .
dependency_check_command: pip-audit --strict
"""


def _seeded(tmp_path: Path, manifest: str | None = _MANIFEST) -> Path:
    """Seed a project workspace, optionally carrying a manifest.

    Returns:
        The base root the reader is handed.
    """
    workspace = tmp_path / "projects" / _PROJECT
    workspace.mkdir(parents=True)
    if manifest is not None:
        (workspace / DEFAULT_MANIFEST_FILENAME).write_text(manifest, encoding="utf-8")
    return tmp_path


class TestRecognisingADeclaredGate:
    @pytest.mark.parametrize(
        ("command", "purpose"),
        [
            pytest.param("ruff check .", CodeExecutionPurpose.LINT, id="lint"),
            pytest.param(
                "ruff format --check .", CodeExecutionPurpose.FORMAT, id="format"
            ),
            pytest.param(
                "pip-audit --strict", CodeExecutionPurpose.DEPENDENCY, id="dependency"
            ),
        ],
    )
    def test_each_declared_command_is_recognised(
        self, tmp_path: Path, command: str, purpose: CodeExecutionPurpose
    ) -> None:
        assert declared_gate_purposes(
            command, workspace_root=_seeded(tmp_path), project_id=_PROJECT
        ) == (purpose,)

    def test_two_gates_sharing_a_program_stay_apart(self, tmp_path: Path) -> None:
        """``ruff check`` and ``ruff format --check`` prove opposite things.

        Matching on the invoked program alone would let a formatter run satisfy
        the lint gate, which is the whole reason the comparison is against the
        declared LINE.
        """
        base = _seeded(tmp_path)

        assert declared_gate_purposes(
            "ruff check .", workspace_root=base, project_id=_PROJECT
        ) != declared_gate_purposes(
            "ruff format --check .", workspace_root=base, project_id=_PROJECT
        )

    def test_respacing_still_matches(self, tmp_path: Path) -> None:
        """An agent that re-spaced the line still ran the gate it declared."""
        assert declared_gate_purposes(
            "ruff   check    .",
            workspace_root=_seeded(tmp_path),
            project_id=_PROJECT,
        ) == (CodeExecutionPurpose.LINT,)

    def test_a_different_argument_is_not_the_declared_gate(
        self, tmp_path: Path
    ) -> None:
        """Narrowing the target is a different run and proves less.

        ``ruff check src`` leaves the rest of the tree unlinted, so accepting it
        as the lint gate would let a unit satisfy the gate over the files it
        chose.
        """
        assert (
            declared_gate_purposes(
                "ruff check src", workspace_root=_seeded(tmp_path), project_id=_PROJECT
            )
            == ()
        )


class TestOneLineRunningSeveralGates:
    """A conjunctive line is evidence for every declaration it satisfies.

    Answering with the first match alone records one receipt and leaves the
    oracle finding none for the other, which refuses a unit whose author ran
    exactly what the project declared.
    """

    def test_a_conjunctive_command_records_both_gates(self, tmp_path: Path) -> None:
        assert set(
            declared_gate_purposes(
                "ruff check . && ruff format --check .",
                workspace_root=_seeded(tmp_path),
                project_id=_PROJECT,
            )
        ) == {CodeExecutionPurpose.LINT, CodeExecutionPurpose.FORMAT}

    def test_a_masked_conjunction_records_neither(self, tmp_path: Path) -> None:
        """``|| true`` exits zero whatever ran, so it proves nothing at all."""
        assert (
            declared_gate_purposes(
                "ruff check . || true",
                workspace_root=_seeded(tmp_path),
                project_id=_PROJECT,
            )
            == ()
        )

    def test_a_shell_payload_hiding_a_mask_records_nothing(
        self, tmp_path: Path
    ) -> None:
        """The mask is inside a quoted payload the outer parse sees as one token.

        Without descending into it the line reads as a single trustworthy
        command, and a linter that failed mints a passing lint receipt.
        """
        assert (
            declared_gate_purposes(
                "bash -c 'ruff check . || true'",
                workspace_root=_seeded(tmp_path),
                project_id=_PROJECT,
            )
            == ()
        )

    def test_a_shell_payload_running_the_gate_is_still_the_gate(
        self, tmp_path: Path
    ) -> None:
        """Descending must not cost the evidence a genuine run produced."""
        assert declared_gate_purposes(
            "bash -c 'ruff check .'",
            workspace_root=_seeded(tmp_path),
            project_id=_PROJECT,
        ) == (CodeExecutionPurpose.LINT,)


class TestWhenThereIsNoDeclaration:
    @pytest.mark.parametrize(
        "manifest",
        [
            pytest.param(None, id="no_manifest"),
            pytest.param(
                "language: python\ntest_command: pytest", id="no_gates_declared"
            ),
            pytest.param("language: [unclosed", id="unparseable"),
        ],
    )
    def test_nothing_is_recognised(self, tmp_path: Path, manifest: str | None) -> None:
        assert (
            declared_gate_purposes(
                "ruff check .",
                workspace_root=_seeded(tmp_path, manifest),
                project_id=_PROJECT,
            )
            == ()
        )

    def test_an_unwired_workspace_recognises_nothing(self) -> None:
        """Guessing at a workspace would invent a purpose from nothing."""
        assert (
            declared_gate_purposes(
                "ruff check .", workspace_root=None, project_id=_PROJECT
            )
            == ()
        )


class TestALineThatRunsNothing:
    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("# ran nothing", id="comment"),
            pytest.param("sh -c ''", id="empty_shell_payload"),
        ],
    )
    def test_it_is_a_run_of_no_gate(self, tmp_path: Path, command: str) -> None:
        """It lexes to no command, and no command is a subset of every gate.

        Read as an empty set it would satisfy each declaration vacuously, so
        every gate the project declares would take a receipt off a line that
        ran nothing at all.
        """
        assert (
            declared_gate_purposes(
                command, workspace_root=_seeded(tmp_path), project_id=_PROJECT
            )
            == ()
        )
