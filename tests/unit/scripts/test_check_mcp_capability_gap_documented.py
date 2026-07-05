"""Tests for the MCP capability-gap discovery gate.

The gate discovers every service an MCP handler depends on (through a
capability-gap guard or a ``*_of`` require_service accessor) and fails when
a backing class is neither constructed in ``src/`` nor tracked in the
ghost-wiring manifest. These tests encode that contract across all three
shipped guard shapes (inline ``is None``, ``_x_wired`` predicate, ``service
= _x_service(...)`` local), the accessor shape, the manifest allowlist, and
the fail-closed behaviour on an unrecognised guard / unparsable file /
unresolvable slice field.
"""

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


class _GateModule(Protocol):
    """Subset of the gate module the tests drive."""

    @staticmethod
    def _run(repo_root: Path) -> int: ...
    @staticmethod
    def _extract_slice_ref(node: ast.AST) -> tuple[str, str] | None: ...
    @staticmethod
    def _annotation_class_name(annotation: ast.expr) -> str | None: ...


def _load_module() -> _GateModule:
    path = _SCRIPTS / "check_mcp_capability_gap_documented.py"
    spec = importlib.util.spec_from_file_location(
        "check_mcp_capability_gap_documented", path
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()

# A slice + ``*_of`` accessor over a ``foo_service`` field. Placed under a
# runtime prefix so the accessor + any construction site are in scope.
_SLICE_WITH_ACCESSOR = """\
class EngineStateSlice:
    foo_service: FooService | None = None


def foo_service_of(app_state):
    return require_service(app_state.slice(EngineStateSlice).foo_service, "Foo")
"""

# The same slice without an accessor: capability-gap guards resolve the
# backing class from the field annotation alone.
_SLICE_ONLY = """\
class EngineStateSlice:
    foo_service: FooService | None = None
"""

_CONSTRUCTION = "def wire():\n    return FooService()\n"

_ACCESSOR_HANDLER = """\
async def _foo_list(app_state):
    return await foo_service_of(app_state).list_items()
"""

_INLINE_GUARD_HANDLER = """\
async def _foo_list(app_state):
    if app_state.slice(EngineStateSlice).foo_service is None:
        return capability_gap("synthorg_foo_list", "foo not wired")
    return "ok"
"""

_PREDICATE_GUARD_HANDLER = """\
def _foo_wired(app_state):
    return app_state.slice(EngineStateSlice).foo_service is not None


async def _foo_list(app_state):
    if not _foo_wired(app_state):
        return capability_gap("synthorg_foo_list", "foo not wired")
    return "ok"
"""

_LOCAL_VAR_GUARD_HANDLER = """\
def _foo_service(app_state):
    if app_state.slice(EngineStateSlice).foo_service is None:
        return None
    return app_state.slice(EngineStateSlice).foo_service


async def _foo_list(app_state):
    service = _foo_service(app_state)
    if service is None:
        return capability_gap("synthorg_foo_list", "foo not wired")
    return "ok"
"""


def _seed(repo: Path, *, manifest: str, files: dict[str, str]) -> None:
    """Write the manifest and any src/synthorg files into a fake repo."""
    manifest_path = repo / "scripts" / "_ghost_wiring_manifest.txt"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest, encoding="utf-8")
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


_STATE = "src/synthorg/engine/state.py"
_WIRING = "src/synthorg/api/wiring.py"
_HANDLER = "src/synthorg/meta/mcp/handlers/foo.py"


def test_accessor_service_constructed_passes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        manifest="",
        files={
            _STATE: _SLICE_WITH_ACCESSOR,
            _WIRING: _CONSTRUCTION,
            _HANDLER: _ACCESSOR_HANDLER,
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_accessor_service_unwired_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_WITH_ACCESSOR, _HANDLER: _ACCESSOR_HANDLER},
    )
    assert _MODULE._run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "FooService" in out
    assert "EngineStateSlice.foo_service" in out


def test_unwired_but_manifested_passes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED FooService #1 -- tracked deferral\n",
        files={_STATE: _SLICE_WITH_ACCESSOR, _HANDLER: _ACCESSOR_HANDLER},
    )
    assert _MODULE._run(tmp_path) == 0


@pytest.mark.parametrize(
    "handler",
    [_INLINE_GUARD_HANDLER, _PREDICATE_GUARD_HANDLER, _LOCAL_VAR_GUARD_HANDLER],
    ids=["inline", "predicate", "local-var"],
)
def test_capability_gap_shapes_discover_unwired_service(
    tmp_path: Path, handler: str
) -> None:
    """Each guard shape resolves to the field, so an unwired service fails."""
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_ONLY, _HANDLER: handler},
    )
    assert _MODULE._run(tmp_path) == 1


@pytest.mark.parametrize(
    "handler",
    [_INLINE_GUARD_HANDLER, _PREDICATE_GUARD_HANDLER, _LOCAL_VAR_GUARD_HANDLER],
    ids=["inline", "predicate", "local-var"],
)
def test_capability_gap_shapes_pass_when_constructed(
    tmp_path: Path, handler: str
) -> None:
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_ONLY, _WIRING: _CONSTRUCTION, _HANDLER: handler},
    )
    assert _MODULE._run(tmp_path) == 0


def test_fail_closed_on_unrecognised_guard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = """\
async def _foo_list(app_state):
    if some_unrelated_flag():
        return capability_gap("synthorg_foo_list", "foo not wired")
    return "ok"
"""
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_ONLY, _WIRING: _CONSTRUCTION, _HANDLER: handler},
    )
    assert _MODULE._run(tmp_path) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_fail_closed_on_unparsable_handler(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_ONLY, _HANDLER: "async def _foo(:\n    pass\n"},
    )
    assert _MODULE._run(tmp_path) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_fail_closed_on_unresolvable_slice_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A guard on a field absent from every slice cannot resolve its class."""
    handler = """\
async def _foo_list(app_state):
    if app_state.slice(EngineStateSlice).absent_service is None:
        return capability_gap("synthorg_foo_list", "absent")
    return "ok"
"""
    _seed(
        tmp_path,
        manifest="",
        files={_STATE: _SLICE_ONLY, _HANDLER: handler},
    )
    assert _MODULE._run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "fail-closed" in out
    assert "absent_service" in out


def test_common_py_is_skipped(tmp_path: Path) -> None:
    """capability_gap in common.py (its definition site) is not scanned."""
    common = """\
def capability_gap(tool, why):
    return "gap"


async def _fallback(app_state):
    if mystery():
        return capability_gap("t", "w")
    return "ok"
"""
    _seed(
        tmp_path,
        manifest="",
        files={
            _STATE: _SLICE_ONLY,
            "src/synthorg/meta/mcp/handlers/common.py": common,
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_extract_slice_ref_matches_slice_access() -> None:
    node = ast.parse("app_state.slice(EngineStateSlice).foo_service").body[0]
    assert isinstance(node, ast.Expr)
    assert _MODULE._extract_slice_ref(node.value) == (
        "EngineStateSlice",
        "foo_service",
    )


def test_extract_slice_ref_ignores_plain_attribute() -> None:
    node = ast.parse("app_state.config").body[0]
    assert isinstance(node, ast.Expr)
    assert _MODULE._extract_slice_ref(node.value) is None


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("FooService | None", "FooService"),
        ("None | FooService", "FooService"),
        ("VersionRepository[Role] | None", "VersionRepository"),
        ("None", None),
    ],
)
def test_annotation_class_name(annotation: str, expected: str | None) -> None:
    node = ast.parse(annotation).body[0]
    assert isinstance(node, ast.Expr)
    assert _MODULE._annotation_class_name(node.value) == expected
