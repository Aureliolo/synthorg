"""Recognising a run of a gate command the project's own manifest declares.

A test runner is known from its program; how a project lints, formats or checks
its dependencies is the project's decision, so it is recognised from the
declaration instead.
"""

from pathlib import Path

import pytest

from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose
from synthorg.tools._declared_gate_runs import declared_gate_purpose

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
        assert (
            declared_gate_purpose(
                command, workspace_root=_seeded(tmp_path), project_id=_PROJECT
            )
            is purpose
        )

    def test_two_gates_sharing_a_program_stay_apart(self, tmp_path: Path) -> None:
        """``ruff check`` and ``ruff format --check`` prove opposite things.

        Matching on the invoked program alone would let a formatter run satisfy
        the lint gate, which is the whole reason the comparison is against the
        declared LINE.
        """
        base = _seeded(tmp_path)

        assert declared_gate_purpose(
            "ruff check .", workspace_root=base, project_id=_PROJECT
        ) is not declared_gate_purpose(
            "ruff format --check .", workspace_root=base, project_id=_PROJECT
        )

    def test_respacing_still_matches(self, tmp_path: Path) -> None:
        """An agent that re-spaced the line still ran the gate it declared."""
        assert (
            declared_gate_purpose(
                "ruff   check    .",
                workspace_root=_seeded(tmp_path),
                project_id=_PROJECT,
            )
            is CodeExecutionPurpose.LINT
        )

    def test_a_different_argument_is_not_the_declared_gate(
        self, tmp_path: Path
    ) -> None:
        """Narrowing the target is a different run and proves less.

        ``ruff check src`` leaves the rest of the tree unlinted, so accepting it
        as the lint gate would let a unit satisfy the gate over the files it
        chose.
        """
        assert (
            declared_gate_purpose(
                "ruff check src", workspace_root=_seeded(tmp_path), project_id=_PROJECT
            )
            is None
        )


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
            declared_gate_purpose(
                "ruff check .",
                workspace_root=_seeded(tmp_path, manifest),
                project_id=_PROJECT,
            )
            is None
        )

    def test_an_unwired_workspace_recognises_nothing(self) -> None:
        """Guessing at a workspace would invent a purpose from nothing."""
        assert (
            declared_gate_purpose(
                "ruff check .", workspace_root=None, project_id=_PROJECT
            )
            is None
        )
