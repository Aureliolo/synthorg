"""Tests for the typed-boundary mock-spec gate.

The gate flags bare ``Mock`` / ``AsyncMock`` / ``MagicMock`` only when
the mock crosses a typed boundary (constructor argument, fixture
return, annotated local). It deliberately ignores ``.return_value =``
chains, attribute-bag scratch objects, and dict / list literal
values; those are the lower rungs of the test-double ladder
(``docs/reference/conventions.md`` section 12.1) and the gate does
not enforce them.
"""

import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class WriteTestFile(Protocol):
    """Callable signature for the ``write_test_file`` fixture."""

    def __call__(self, content: str, name: str = ...) -> Path: ...


class _CheckMockSpecModule(Protocol):
    """Subset of ``scripts/check_mock_spec.py`` the tests exercise."""

    InspectionError: type[Exception]
    _TESTS_ROOT: Path

    @staticmethod
    def _scan_file(path: Path) -> list[tuple[int, int]]: ...
    @staticmethod
    def cmd_scan_paths(paths: Iterable[str]) -> int: ...


def _load_module() -> _CheckMockSpecModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_mock_spec.py"
    spec = importlib.util.spec_from_file_location("check_mock_spec", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_CheckMockSpecModule, module)


_MODULE = _load_module()


@pytest.fixture
def write_test_file(tmp_path: Path) -> WriteTestFile:
    def _write(content: str, name: str = "sample.py") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


# ---------------------------------------------------------------------
# Pattern A: typed-boundary substitutions (CATCH)
# ---------------------------------------------------------------------


def test_positional_arg_to_non_mock_call_caught(
    write_test_file: WriteTestFile,
) -> None:
    src = "from unittest.mock import Mock\nclass Service: ...\nService(Mock())\n"
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 3


def test_keyword_arg_to_non_mock_call_caught(
    write_test_file: WriteTestFile,
) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "Service(deps=Mock())\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 4


def test_named_var_passed_to_typed_constructor_skipped(
    write_test_file: WriteTestFile,
) -> None:
    """Indirect substitution (name binding then arg-pass) is NOT caught.

    Tracking name-usage across a function body precisely requires
    resolving callee parameter annotations (cross-module). Out of
    scope for the pure-AST gate; covered by the test-double ladder
    convention rather than gated enforcement.
    """
    src = (
        "from unittest.mock import Mock\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "def test_x():\n"
        "    m = Mock()\n"
        "    Service(deps=m)\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_chained_assignment_skipped(
    write_test_file: WriteTestFile,
) -> None:
    """Same rationale as the named-var case: name tracking is OOS."""
    src = (
        "from unittest.mock import Mock\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "def test_x():\n"
        "    a = b = Mock()\n"
        "    Service(deps=a)\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_walrus_in_call_arg_caught(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "def test_x():\n"
        "    Service(deps=(m := Mock()))\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 5


def test_annassign_concrete_type_caught(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Service: ...\n"
        "def test_x():\n"
        "    m: Service = Mock()\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 4


def test_return_typed_fixture_caught(write_test_file: WriteTestFile) -> None:
    src = (
        "import pytest\n"
        "from unittest.mock import Mock\n"
        "class Service: ...\n"
        "@pytest.fixture\n"
        "def svc() -> Service:\n"
        "    return Mock()\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 6


def test_yield_typed_fixture_caught(write_test_file: WriteTestFile) -> None:
    src = (
        "import pytest\n"
        "from unittest.mock import Mock\n"
        "class Service: ...\n"
        "@pytest.fixture\n"
        "def svc() -> Service:\n"
        "    yield Mock()\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 6


def test_async_function_typed_return_caught(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Service: ...\n"
        "async def factory() -> Service:\n"
        "    return Mock()\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 4


def test_yield_from_typed_return_caught(write_test_file: WriteTestFile) -> None:
    """`yield from Mock()` in a typed-return generator is caught.

    Locks the `ast.YieldFrom` branch of `_decide_direct`. Without this
    test, removing `YieldFrom` from the parent-isinstance tuple would
    silently weaken the gate.
    """
    src = (
        "from collections.abc import Iterator\n"
        "from unittest.mock import Mock\n"
        "class Service: ...\n"
        "def factory() -> Iterator[Service]:\n"
        "    yield from Mock()\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 5


def test_propertymock_recognised_as_mock_class(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import PropertyMock\n"
        "class Foo: ...\n"
        "def make(p): return p\n"
        "make(p=PropertyMock())\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1
    assert hits[0][0] == 4


def test_propertymock_with_spec_not_flagged(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import PropertyMock\n"
        "class Foo: ...\n"
        "def make(p): return p\n"
        "make(p=PropertyMock(spec=Foo))\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


# ---------------------------------------------------------------------
# Pattern B / C / D: noisy-but-not-typed substitution (SKIP)
# ---------------------------------------------------------------------


def test_inner_mock_in_mock_class_call_skipped(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Foo: ...\n"
        "x = Mock(spec=Foo, return_value=Mock(), wraps=Mock())\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_create_autospec_inner_mock_skipped(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock, create_autospec\n"
        "class Foo: ...\n"
        "x = create_autospec(Foo, instance=True, return_value=Mock())\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_mock_of_subscript_factory_skipped(write_test_file: WriteTestFile) -> None:
    """``mock_of[T](return_value=Mock())`` resolves to a factory call.

    Locks the ``ast.Subscript`` recursion in ``_terminal_callee_name``
    and the presence of ``"mock_of"`` in ``_MOCK_FACTORY_NAMES`` so
    the typed-factory subscript form is treated identically to
    ``create_autospec``.
    """
    src = (
        "from unittest.mock import Mock\n"
        "class _Factory:\n"
        "    def __getitem__(self, t): return lambda **kw: t()\n"
        "mock_of = _Factory()\n"
        "class Foo: ...\n"
        "x = mock_of[Foo](return_value=Mock())\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_return_value_chain_skipped(write_test_file: WriteTestFile) -> None:
    """``x.return_value = Mock()`` is attribute reconfiguration, not a boundary.

    Pairs with ``test_inner_mock_in_mock_class_call_skipped`` so the
    two ways of wiring a child mock onto a parent (kwarg at
    construction, attribute write afterwards) are both locked as SKIP.
    """
    src = "from unittest.mock import Mock\nx = Mock()\nx.return_value = Mock()\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_attr_assign_to_mock_skipped(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import AsyncMock, Mock\n"
        "class Foo: ...\n"
        "def test_x():\n"
        "    m = Mock(spec=Foo)\n"
        "    m.method = AsyncMock()\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_attribute_bag_skipped(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import MagicMock\n"
        "def test_x():\n"
        "    m = MagicMock()\n"
        "    m.role = 'eng'\n"
        "    m.dept = 'r&d'\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_dict_value_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import MagicMock\nstate = {'app': MagicMock()}\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_list_element_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import MagicMock\nxs = [MagicMock(), MagicMock()]\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_tuple_element_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import MagicMock\nxs = (MagicMock(), MagicMock())\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_set_element_skipped(write_test_file: WriteTestFile) -> None:
    """Set literals are part of the collection-skip branch.

    Locks `ast.Set` in the `(ast.List, ast.Tuple, ast.Set, ast.Dict)`
    parent-isinstance tuple. Removing `Set` would flip these sites
    into spurious CATCH verdicts.
    """
    src = "from unittest.mock import MagicMock\nxs = {MagicMock(), MagicMock()}\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_named_var_only_used_as_attr_bag_skipped(
    write_test_file: WriteTestFile,
) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "def test_x():\n"
        "    m = Mock()\n"
        "    m.x = 1\n"
        "    m.y = 2\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_named_var_passed_to_mock_class_skipped(
    write_test_file: WriteTestFile,
) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Foo: ...\n"
        "def test_x():\n"
        "    m = Mock()\n"
        "    Mock(spec=Foo, return_value=m)\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_annassign_mock_typed_skipped(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import AsyncMock, Mock\n"
        "from typing import Any\n"
        "def test_x():\n"
        "    a: Mock = Mock()\n"
        "    b: AsyncMock = AsyncMock()\n"
        "    c: Any = Mock()\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_return_unannotated_fn_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import Mock\ndef factory():\n    return Mock()\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_class_scope_assignment_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import Mock\nclass TestThing:\n    m = Mock()\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_module_scope_unused_skipped(write_test_file: WriteTestFile) -> None:
    src = "from unittest.mock import Mock\n_unused = Mock()\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


# ---------------------------------------------------------------------
# Core spec-detection cases
# ---------------------------------------------------------------------


def test_specced_mock_ignored(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import AsyncMock\n"
        "class Foo: ...\n"
        "def test_x():\n"
        "    m: Foo = AsyncMock(spec=Foo)\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_positional_first_arg_treated_as_spec(write_test_file: WriteTestFile) -> None:
    src = (
        "from unittest.mock import Mock\n"
        "class Foo: ...\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "Service(deps=Mock(Foo))\n"
    )
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_attribute_call_form_detected(write_test_file: WriteTestFile) -> None:
    src = (
        "import unittest.mock as mock\n"
        "class Service:\n"
        "    def __init__(self, deps): self.deps = deps\n"
        "Service(deps=mock.MagicMock())\n"
    )
    hits = _MODULE._scan_file(write_test_file(src))
    assert len(hits) == 1


def test_non_mock_calls_not_flagged(write_test_file: WriteTestFile) -> None:
    src = "x = list()\ny = dict()\n"
    assert _MODULE._scan_file(write_test_file(src)) == []


def test_unparseable_file_raises(write_test_file: WriteTestFile) -> None:
    src = "def broken(:\n"
    with pytest.raises(_MODULE.InspectionError):
        _MODULE._scan_file(write_test_file(src))


def test_shared_dir_still_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cmd_scan_paths`` skips ``tests/_shared/`` like ``--scan-all`` does."""
    tests_root = tmp_path / "tests"
    shared = tests_root / "_shared"
    shared.mkdir(parents=True)
    bad_file = shared / "fake.py"
    bad_file.write_text(
        "from unittest.mock import Mock\nclass Service: ...\nService(Mock())\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "_TESTS_ROOT", tests_root)
    rc = _MODULE.cmd_scan_paths([str(bad_file)])
    assert rc == 0
