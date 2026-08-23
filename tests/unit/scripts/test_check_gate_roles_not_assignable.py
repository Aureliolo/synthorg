"""Unit tests for ``scripts/check_gate_roles_not_assignable.py``.

The violating fixture is the shape that actually shipped: the recursion-depth
sweep's ``SweepRoster.roles`` carried its own comprehension over builders AND
reviewers, and it fed the sweep's own planner, so the exclusion was enforced in
the product and bypassed in the harness measuring the product. It is kept
verbatim rather than paraphrased.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_synthetic_cost_owner.py``.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var for the duration of each test.

    The gate's ``git ls-files`` subprocess inherits this process's
    environment. Under a pre-push hook ``GIT_DIR`` / ``GIT_WORK_TREE`` point
    at the real repo, which would let the scan escape the ``tmp_path``
    sandbox and read the live tree. A test must NEVER touch real repo data.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_gate_roles_not_assignable.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    col: int
    qualname: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _OWNER_REL: str

    @staticmethod
    def _scan_file(path: Path, rel: str) -> list[_HitView]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def _check_owner(project_root: Path) -> None: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_gate_roles_not_assignable",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


class WritePy(Protocol):
    """Callable signature of the ``write_py`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


@pytest.fixture
def write_py(tmp_path: Path) -> WritePy:
    """Helper that writes a Python source string to ``tmp_path/<name>``."""

    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


# Verbatim: the roster property that fed the sweep's planner a gate role.
_SECOND_DERIVATION = """\
class SweepRoster:
    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(
            sorted({agent.role for agent in (*self.builders, *self.reviewers)})
        )
"""

_BARE_SET_COMPREHENSION = """\
def roles(agents):
    return {agent.role for agent in agents}
"""

_LIST_COMPREHENSION_VIA_STR = """\
def roles(agents):
    return sorted([str(a.role) for a in agents])
"""

_GENERATOR_INTO_FROZENSET = """\
def roles(agents):
    return frozenset(a.role for a in agents)
"""


@pytest.mark.parametrize(
    "source",
    [
        _SECOND_DERIVATION,
        _BARE_SET_COMPREHENSION,
        _LIST_COMPREHENSION_VIA_STR,
        _GENERATOR_INTO_FROZENSET,
    ],
    ids=["shipped-sweep-roster", "bare-set", "list-via-str", "generator-frozenset"],
)
def test_flags_every_second_derivation(write_py: WritePy, source: str) -> None:
    """Each wrapping of a role comprehension is one roster too many."""
    path = write_py(source)
    hits = _MODULE._scan_file(path, "sample.py")
    assert len(hits) == 1
    assert "roles" in hits[0].qualname
    assert "roster_from_agents" in hits[0].message()


# Every one of these is in the tree today and must stay passing.
_CALLS_THE_OWNER = """\
def roles(agents):
    return roster_from_agents(agents)
"""

_PASSES_A_ROSTER_THROUGH = """\
def build(context):
    return DecompositionContext(available_roles=context.available_roles)
"""

_ROLES_AS_A_KEYWORD_NOT_A_ROSTER = """\
def report(panel, admitted):
    log(reviewer_roles=[a.role for a in panel])
    log(sanctioned_roles=sorted({a.role for a in admitted}))
"""

_ID_TO_ROLE_INDEX = """\
def index(agents):
    return {str(agent.id): normalize_identifier(str(agent.role)) for agent in agents}
"""

_FILTERS_AGENTS_NOT_ROLES = """\
def matching(active, role):
    return [a for a in active if compare_ci(a.role, role)]
"""


@pytest.mark.parametrize(
    "source",
    [
        _CALLS_THE_OWNER,
        _PASSES_A_ROSTER_THROUGH,
        _ROLES_AS_A_KEYWORD_NOT_A_ROSTER,
        _ID_TO_ROLE_INDEX,
        _FILTERS_AGENTS_NOT_ROLES,
    ],
    ids=["owner", "pass-through", "keyword", "id-to-role-map", "agent-filter"],
)
def test_accepts_what_does_not_derive_a_roster(write_py: WritePy, source: str) -> None:
    """The rule is about deriving a roster, not about handling roles."""
    path = write_py(source)
    assert _MODULE._scan_file(path, "sample.py") == []


def test_per_line_marker_suppresses(write_py: WritePy) -> None:
    """A justified marker anywhere in the return's span suppresses the hit."""
    path = write_py(
        "def roles(agents):\n"
        "    return sorted(\n"
        "        {a.role for a in agents}\n"
        "    )  # lint-allow: gate-role-assignable -- rendered in a report\n"
    )
    assert _MODULE._scan_file(path, "sample.py") == []


def test_a_marker_does_not_suppress_a_hit_it_is_not_attached_to(
    write_py: WritePy,
) -> None:
    """Suppression is scoped to the return's own line span, not the file.

    The complement of the test above, and the half that decides whether the
    opt-out is safe: one justified derivation must not silence every other
    derivation beside it, or a single marker disarms the module.
    """
    path = write_py(
        "def rendered(agents):\n"
        "    return sorted(\n"
        "        {a.role for a in agents}\n"
        "    )  # lint-allow: gate-role-assignable -- rendered in a report\n"
        "\n"
        "\n"
        "def offered(agents):\n"
        "    return sorted({a.role for a in agents})\n"
    )
    hits = _MODULE._scan_file(path, "sample.py")
    assert [hit.qualname for hit in hits] == ["offered"]


def test_a_roster_named_before_it_is_returned_is_still_a_derivation(
    write_py: WritePy,
) -> None:
    """Splitting the derivation across two statements is the same derivation.

    Reading only the return expression lets one line build the roster and the
    next hand it back, which is the shape a refactor reaches for first.
    """
    path = write_py(
        "def roles(agents):\n"
        "    roles = {agent.role for agent in agents}\n"
        "    return tuple(sorted(roles))\n"
    )
    hits = _MODULE._scan_file(path, "sample.py")
    assert [hit.qualname for hit in hits] == ["roles"]


def test_a_name_bound_to_something_else_is_not_a_roster(write_py: WritePy) -> None:
    """The trace is about role comprehensions, not about every local."""
    path = write_py(
        "def titles(agents):\n"
        "    names = {agent.name for agent in agents}\n"
        "    return tuple(sorted(names))\n"
    )
    assert _MODULE._scan_file(path, "sample.py") == []


def test_a_function_defined_under_a_conditional_is_still_scanned(
    write_py: WritePy,
) -> None:
    """A ``def`` in an ``if`` or a ``try`` is a function like any other.

    Descending only into class and function bodies leaves a whole shape of
    definition unscanned, which for a gate carrying no baseline and no opt-out
    is the difference between reporting nothing and there being nothing.
    """
    path = write_py(
        "import os\n"
        "\n"
        "if os.name == 'nt':\n"
        "    def roles(agents):\n"
        "        return sorted({a.role for a in agents})\n"
        "else:\n"
        "    try:\n"
        "        def other(agents):\n"
        "            return {a.role for a in agents}\n"
        "    except ImportError:\n"
        "        pass\n"
    )
    hits = _MODULE._scan_file(path, "sample.py")
    assert sorted(hit.qualname for hit in hits) == ["other", "roles"]


@pytest.mark.parametrize(
    "comment",
    [
        "# lint-allow: gate-role-assignable",
        "# lint-allow: gate-role-assignable --",
        "# lint-allow: gate-role-assignable --   ",
    ],
    ids=["bare", "dashes-only", "blank-reason"],
)
def test_marker_requires_a_justification(write_py: WritePy, comment: str) -> None:
    """An unjustified marker is not a marker: every case is a claim to record."""
    assert not _MODULE._is_valid_marker(comment)
    path = write_py(
        f"def roles(agents):\n    return {{a.role for a in agents}}  {comment}\n"
    )
    assert len(_MODULE._scan_file(path, "sample.py")) == 1


def test_unreadable_source_fails_closed(write_py: WritePy, tmp_path: Path) -> None:
    """A file that will not parse fails the gate rather than passing silently."""
    path = write_py("def roles(agents:\n    return\n")
    _write_owner(tmp_path)
    assert _MODULE.main(["--repo-root", str(tmp_path), "--files", str(path)]) == 2


def _write_owner(root: Path, *, body: str | None = None) -> None:
    """Materialise a stand-in owner module under *root*."""
    owner = root / _MODULE._OWNER_REL
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text(
        body
        if body is not None
        else (
            "def roster_from_agents(agents):\n"
            "    return tuple(\n"
            "        sorted(\n"
            "            {\n"
            "                a.role\n"
            "                for a in agents\n"
            "                if not role_is_gate_role(str(a.role))\n"
            "            }\n"
            "        )\n"
            "    )\n"
        ),
        encoding="utf-8",
    )


def test_clean_tree_returns_zero(tmp_path: Path, write_py: WritePy) -> None:
    """An intact owner plus a clean file is a pass."""
    _write_owner(tmp_path)
    path = write_py("def roles(agents):\n    return roster_from_agents(agents)\n")
    assert _MODULE.main(["--repo-root", str(tmp_path), "--files", str(path)]) == 0


def test_violation_returns_one(tmp_path: Path, write_py: WritePy) -> None:
    """A second derivation fails the gate."""
    _write_owner(tmp_path)
    path = write_py(_SECOND_DERIVATION)
    assert _MODULE.main(["--repo-root", str(tmp_path), "--files", str(path)]) == 1


@pytest.mark.parametrize(
    "body",
    [
        None,
        "def something_else(agents):\n    return ()\n",
        (
            "def roster_from_agents(agents):\n"
            "    return tuple(sorted({a.role for a in agents}))\n"
        ),
        # The guard is called, but by a helper the owner never invokes, so the
        # roster it answers with has not passed through it.
        (
            "def roster_from_agents(agents):\n"
            "    def _unused(role):\n"
            "        return role_is_gate_role(role)\n"
            "    return tuple(sorted({a.role for a in agents}))\n"
        ),
        # Two definitions: the guarded one is replaced by an unguarded one, and
        # only the survivor decides what a planner is offered.
        (
            "def roster_from_agents(agents):\n"
            "    kept = {a.role for a in agents if not role_is_gate_role(a.role)}\n"
            "    return tuple(sorted(kept))\n"
            "\n"
            "def roster_from_agents(agents):\n"
            "    return tuple(sorted({a.role for a in agents}))\n"
        ),
    ],
    ids=["present", "function-gone", "guard-gone", "guard-unreachable", "shadowed"],
)
def test_owner_must_still_enforce(tmp_path: Path, body: str | None) -> None:
    """An owner that stopped excluding gate roles is a configuration error.

    Exit 2 rather than 1: the gate cannot report honestly on anything else once
    the one place the exclusion lives has stopped doing it.
    """
    _write_owner(tmp_path, body=body)
    expected = 0 if body is None else 2
    assert _MODULE.main(["--repo-root", str(tmp_path), "--files"]) == expected


def test_missing_owner_is_a_configuration_error(tmp_path: Path) -> None:
    """No owner module at all fails closed."""
    assert _MODULE.main(["--repo-root", str(tmp_path), "--files"]) == 2


def test_live_tree_is_clean() -> None:
    """The real tree passes: the gate ships with no baseline."""
    assert _MODULE.main([]) == 0
