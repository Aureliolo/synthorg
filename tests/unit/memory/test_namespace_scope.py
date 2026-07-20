"""Project-scoped namespace derivation for memory reads and writes.

These are the guard for the cross-project bleed H1 closed: a project's
captured memory must land in its own namespace, and a project-scoped
read must see the shared default unioned with that project alone.
"""

import pytest

from synthorg.core.execution_identity import run_identity_scope
from synthorg.core.types import NotBlankStr
from synthorg.memory.namespace_scope import (
    DEFAULT_MEMORY_NAMESPACE,
    PROJECT_NAMESPACE_PREFIX,
    ambient_read_namespaces,
    ambient_write_namespace,
    read_namespaces,
    write_namespace,
)

pytestmark = pytest.mark.unit


class TestWriteNamespace:
    """A capture's namespace is decided solely by its project scope."""

    def test_project_scoped_write_lands_in_project_namespace(self) -> None:
        assert write_namespace("proj-a") == f"{PROJECT_NAMESPACE_PREFIX}proj-a"

    def test_unscoped_write_lands_in_default(self) -> None:
        assert write_namespace(None) == DEFAULT_MEMORY_NAMESPACE

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_project_is_treated_as_unscoped(self, blank: str) -> None:
        assert write_namespace(blank) == DEFAULT_MEMORY_NAMESPACE

    def test_surrounding_whitespace_is_stripped(self) -> None:
        assert write_namespace("  proj-a  ") == f"{PROJECT_NAMESPACE_PREFIX}proj-a"


class TestReadNamespaces:
    """A recall's scope unions the agent's default with its project."""

    def test_unscoped_read_sees_all_namespaces(self) -> None:
        assert read_namespaces(None) is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_project_reads_all_namespaces(self, blank: str) -> None:
        assert read_namespaces(blank) is None

    def test_project_read_unions_default_and_project(self) -> None:
        scope = read_namespaces("proj-a")
        assert scope == frozenset(
            {
                NotBlankStr(DEFAULT_MEMORY_NAMESPACE),
                NotBlankStr(f"{PROJECT_NAMESPACE_PREFIX}proj-a"),
            }
        )

    def test_one_project_never_sees_another(self) -> None:
        a = read_namespaces("proj-a")
        b = read_namespaces("proj-b")
        assert a is not None
        assert b is not None
        # The only shared namespace is the agent's own default; neither
        # project's namespace appears in the other's read scope.
        assert a & b == frozenset({NotBlankStr(DEFAULT_MEMORY_NAMESPACE)})


class TestWriteInsideReadScope:
    """A project write is always visible to that project's own recall."""

    def test_project_write_namespace_is_in_its_read_scope(self) -> None:
        scope = read_namespaces("proj-a")
        assert scope is not None
        assert write_namespace("proj-a") in scope

    def test_default_write_namespace_is_in_every_project_read_scope(self) -> None:
        # Unscoped (personal) memory stays visible inside any project.
        assert write_namespace(None) in read_namespaces("proj-a")  # type: ignore[operator]


class TestAmbientScope:
    """The ambient helpers read the run's bound execution identity."""

    def test_ambient_write_defaults_without_a_bound_identity(self) -> None:
        assert ambient_write_namespace() == DEFAULT_MEMORY_NAMESPACE

    def test_ambient_read_is_unscoped_without_a_bound_identity(self) -> None:
        assert ambient_read_namespaces() is None

    def test_ambient_write_follows_the_bound_project(self) -> None:
        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id="proj-a",
        ):
            assert ambient_write_namespace() == f"{PROJECT_NAMESPACE_PREFIX}proj-a"

    def test_ambient_read_follows_the_bound_project(self) -> None:
        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id="proj-a",
        ):
            assert ambient_read_namespaces() == read_namespaces("proj-a")

    def test_ambient_scope_is_restored_on_exit(self) -> None:
        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id="proj-a",
        ):
            pass
        assert ambient_write_namespace() == DEFAULT_MEMORY_NAMESPACE
        assert ambient_read_namespaces() is None
