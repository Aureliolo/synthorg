"""Unit tests for ``scripts/check_no_ghost_attribute_read.py``.

The two source lines the gate exists for are kept verbatim rather than
paraphrased, because a paraphrase would test the gate against a tidied-up
idea of the defect instead of against the defect. Both shipped, both read an
attribute that exists on nothing this tree defines, and both turned a missing
attribute into a plausible ``None`` that the code below then trusted.

Tests load the script via :mod:`importlib` and call its helpers directly,
matching ``test_check_no_synthetic_cost_owner.py``.
"""

import ast
import importlib.util
import os
import sys
from collections.abc import Iterable
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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_ghost_attribute_read.py"


class _HitView(Protocol):
    """Structural view of the script's ``_Hit`` class."""

    rel: str
    lineno: int
    col: int
    attribute: str
    qualname: str

    @property
    def group_key(self) -> str: ...
    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def declared_attribute_names(
        parsed: Iterable[tuple[Path, ast.Module]],
    ) -> set[str]: ...
    @staticmethod
    def scan_file(path: Path, rel: str, declared: frozenset[str]) -> list[_HitView]: ...
    @staticmethod
    def _is_valid_marker(comment_token: str) -> bool: ...
    @staticmethod
    def cmd_scan(project_root: Path, files: list[Path]) -> int: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_no_ghost_attribute_read",
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


# ── the two live defects, verbatim ──────────────────────────────

# observability/_prometheus_label_fetchers.py:197 before 4b332aaf3.
# ``AppState`` has never carried a tool registry, so every scrape resolved
# the read to None and reported an empty tool allowlist as a success.
_D21 = """\
def _fetch() -> frozenset[str] | None:
    registry = getattr(app_state, "tool_registry", None)
    if registry is None:
        return None
    return frozenset(t.name for t in registry.all_tools())
"""

# api/controllers/_requester.py:29 before 4b332aaf3. The authenticated user
# lives on the connection, not on application State, so every audited write
# recorded "api" instead of the operator.
_D23 = """\
def requester(state: State) -> str:
    user = getattr(state, "_connection_user", None)
    if user is None:
        return "api"
    return user.username
"""


@pytest.mark.parametrize(
    ("source", "attribute"),
    [(_D21, "tool_registry"), (_D23, "_connection_user")],
    ids=["D21", "D23"],
)
def test_flags_the_two_live_defects(
    write_py: WritePy, source: str, attribute: str
) -> None:
    path = write_py(source)
    hits = _MODULE.scan_file(path, "sample.py", frozenset())
    assert len(hits) == 1
    assert hits[0].attribute == attribute
    assert attribute in hits[0].message()


def test_accepts_a_name_the_tree_declares(write_py: WritePy) -> None:
    # The gate answers "does this name exist on anything we define"; whether
    # it exists on THIS object needs inference and is mypy's question.
    path = write_py(_D21)
    assert _MODULE.scan_file(path, "sample.py", frozenset({"tool_registry"})) == []


_TWO_ARG = """\
def read(obj: object) -> object:
    return getattr(obj, "ghost")
"""

_NON_LITERAL = """\
def read(obj: object, field: str) -> object:
    return getattr(obj, field, None)
"""

_HASATTR = """\
def read(obj: object) -> bool:
    return hasattr(obj, "ghost")
"""

_DICT_GET = """\
def read(data: dict[str, object]) -> object:
    return data.get("ghost", None)
"""

_KEYWORD_DEFAULT = """\
def read(obj: object) -> object:
    # getattr takes no keyword arguments, so this is somebody else's getattr.
    return getattr(obj, "ghost", default=None)
"""


@pytest.mark.parametrize(
    "source",
    [_TWO_ARG, _NON_LITERAL, _HASATTR, _DICT_GET, _KEYWORD_DEFAULT],
    ids=["two-arg", "non-literal", "hasattr", "dict-get", "keyword-default"],
)
def test_ignores_shapes_outside_the_rule(write_py: WritePy, source: str) -> None:
    path = write_py(source)
    assert _MODULE.scan_file(path, "sample.py", frozenset()) == []


_SUPPRESSED = """\
def read(exc: Exception) -> str | None:
    return getattr(  # lint-allow: ghost-attribute-read -- psycopg Diagnostic
        exc.diag, "constraint_name", None
    )
"""


def test_marker_suppresses_anywhere_in_the_call_span(write_py: WritePy) -> None:
    path = write_py(_SUPPRESSED)
    assert _MODULE.scan_file(path, "sample.py", frozenset()) == []


@pytest.mark.parametrize(
    ("comment", "valid"),
    [
        ("# lint-allow: ghost-attribute-read -- a psycopg Diagnostic field", True),
        ("# lint-allow: ghost-attribute-read --", False),
        ("# lint-allow: ghost-attribute-read", False),
        ("# lint-allow: something-else -- reason", False),
    ],
)
def test_marker_requires_a_justification(comment: str, valid: bool) -> None:
    assert _MODULE._is_valid_marker(comment) is valid


_DECLARATIONS = """\
class Holder:
    annotated: int = 0
    assigned = 1
    paired_a = paired_b = 2
    tuple_a, tuple_b = (3, 4)
    __slots__ = ("slotted",)

    class Inner:
        pass

    def method(self) -> None:
        self.instance_attr = 2
        self.annotated_instance: int = 3
        self.unpacked_first, self.unpacked_second = compute()
        [self.list_unpacked] = [1]
        self.augmented += 1
        for self.looped in ():
            pass
        with open("x") as self.managed:
            pass

    @property
    def derived(self) -> int:
        return 0


def install(target: object) -> None:
    setattr(target, "set_by_name", 4)
"""

_SECOND_MODULE = """\
class Elsewhere:
    declared_in_another_file: int = 0
"""


def test_declaration_pass_collects_every_shape(write_py: WritePy) -> None:
    """Every binding form the tree actually uses counts as a declaration.

    A form the pass misses is worse than one it over-collects: the name is
    genuinely declared, so the gate would report an honest read as a ghost
    and the developer has no way to satisfy it except a false suppression.
    """
    path = write_py(_DECLARATIONS, name="decls.py")
    other = write_py(_SECOND_MODULE, name="other.py")
    # Many files, not one: the set is a tree-wide union, and a pass that
    # only ever saw a single module would look correct while collapsing it.
    declared = _MODULE.declared_attribute_names(
        [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in (path, other)]
    )
    assert {
        "annotated",
        "assigned",
        "paired_a",
        "paired_b",
        "tuple_a",
        "tuple_b",
        "slotted",
        "Inner",
        "method",
        "instance_attr",
        "annotated_instance",
        "unpacked_first",
        "unpacked_second",
        "list_unpacked",
        "augmented",
        "looped",
        "managed",
        "derived",
        "set_by_name",
        "declared_in_another_file",
    } <= declared


def test_unpacked_instance_attribute_is_not_a_ghost(make_tree: MakeTree) -> None:
    """A name bound only by tuple unpacking is declared, so reads of it pass.

    The shape is live in this tree (a scheduler and a claim worker both bind
    a pair that way), so missing it would have failed real code.
    """
    root = make_tree(
        "class Pair:\n"
        "    def __init__(self) -> None:\n"
        "        self.left, self.right = (1, 2)\n"
        "\n"
        "def read(p: object) -> object:\n"
        '    return getattr(p, "left", None)\n'
    )
    assert _MODULE.cmd_scan(root, []) == 0


# ── end to end, over a sandbox tree ─────────────────────────────


class MakeTree(Protocol):
    """Callable signature of the ``make_tree`` fixture."""

    def __call__(self, source: str, *, baseline: str | None = ...) -> Path: ...


@pytest.fixture
def make_tree(tmp_path: Path) -> MakeTree:
    """Build a sandbox repo root holding one scanned module."""

    def _make(source: str, *, baseline: str | None = None) -> Path:
        package = tmp_path / "src" / "synthorg"
        package.mkdir(parents=True)
        (package / "sample.py").write_text(source, encoding="utf-8")
        if baseline is not None:
            scripts = tmp_path / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            (scripts / "ghost_attribute_read_baseline.txt").write_text(
                baseline, encoding="utf-8"
            )
        return tmp_path

    return _make


_CLEAN = """\
class Holder:
    registry: object = None


def read(holder: Holder) -> object:
    return getattr(holder, "registry", None)
"""


def test_clean_tree_returns_zero(make_tree: MakeTree) -> None:
    root = make_tree(_CLEAN)
    assert _MODULE.main(["--repo-root", str(root)]) == 0


def test_ghost_returns_one_and_names_the_site(
    make_tree: MakeTree, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_tree(_D21)
    assert _MODULE.main(["--repo-root", str(root)]) == 1
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "src/synthorg/sample.py:2" in combined
    assert "tool_registry" in combined
    assert "lint-allow: ghost-attribute-read" in combined


def test_unparseable_source_returns_two(make_tree: MakeTree) -> None:
    root = make_tree("def broken(:\n    pass\n")
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_missing_repo_root_returns_two(tmp_path: Path) -> None:
    assert _MODULE.main(["--repo-root", str(tmp_path / "nope")]) == 2


def test_malformed_baseline_returns_two(make_tree: MakeTree) -> None:
    root = make_tree(_D21, baseline="this is not an entry\n")
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_stale_baseline_entry_returns_two(make_tree: MakeTree) -> None:
    # A baseline count that outlives its sites pre-authorises future ghosts,
    # so a shrink is drift to be regenerated, not silently tolerated.
    root = make_tree(_CLEAN, baseline="src/synthorg/sample.py::read::registry::1\n")
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_baselined_ghost_passes(make_tree: MakeTree) -> None:
    root = make_tree(
        _D21, baseline="src/synthorg/sample.py::_fetch::tool_registry::1\n"
    )
    assert _MODULE.main(["--repo-root", str(root)]) == 0


_TWO_GHOSTS_ONE_FUNCTION = """\
def _fetch() -> tuple[object, object]:
    registry = getattr(app_state, "tool_registry", None)
    other = getattr(app_state, "tool_registry", None)
    return registry, other
"""


def test_second_ghost_in_a_baselined_function_fails(make_tree: MakeTree) -> None:
    root = make_tree(
        _TWO_GHOSTS_ONE_FUNCTION,
        baseline="src/synthorg/sample.py::_fetch::tool_registry::1\n",
    )
    assert _MODULE.main(["--repo-root", str(root)]) == 1


def test_update_writes_a_passing_baseline(make_tree: MakeTree) -> None:
    root = make_tree(_D21)
    assert _MODULE.main(["--repo-root", str(root), "--update"]) == 0
    written = (root / "scripts" / "ghost_attribute_read_baseline.txt").read_text(
        encoding="utf-8"
    )
    assert "src/synthorg/sample.py::_fetch::tool_registry::1" in written
    assert _MODULE.main(["--repo-root", str(root)]) == 0


def test_update_leaves_the_baseline_untouched_when_the_scan_fails(
    make_tree: MakeTree,
) -> None:
    root = make_tree("def broken(:\n", baseline="# kept\n")
    assert _MODULE.main(["--repo-root", str(root), "--update"]) == 2
    assert (root / "scripts" / "ghost_attribute_read_baseline.txt").read_text(
        encoding="utf-8"
    ) == "# kept\n"


def test_files_mode_scans_only_the_named_file(make_tree: MakeTree) -> None:
    root = make_tree(_D21)
    clean = root / "src" / "synthorg" / "other.py"
    clean.write_text(_CLEAN, encoding="utf-8")
    assert _MODULE.main(["--repo-root", str(root), "--files", str(clean)]) == 0
    assert (
        _MODULE.main(
            [
                "--repo-root",
                str(root),
                "--files",
                str(root / "src" / "synthorg" / "sample.py"),
            ]
        )
        == 1
    )


def test_live_tree_matches_its_baseline() -> None:
    # The shipped baseline is the tree's history; a new ghost read fails here
    # rather than at push time, and a removed one is drift to regenerate.
    assert _MODULE.main([]) == 0
