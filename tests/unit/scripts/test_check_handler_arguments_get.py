"""Tests for the MCP typed-args ``arguments``-access AST gate."""

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _ViolationLike:
    """Mirror of the gate's internal _Violation dataclass for type-checked tests."""

    rel_path: str
    lineno: int
    message: str

    def render(self) -> str:
        return f"{self.rel_path}:{self.lineno}: {self.message}"


class _GateModule(Protocol):
    """Subset of ``scripts/check_handler_arguments_get.py`` the tests exercise."""

    @staticmethod
    def _run_gate(
        domains_root: Path,
        handlers_root: Path,
    ) -> list[_ViolationLike]: ...

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_module() -> _GateModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_handler_arguments_get.py"
    spec = importlib.util.spec_from_file_location(
        "check_handler_arguments_get",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


# ── Fixture builder ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Tree:
    """A synthetic domains/ + handlers/ tree under tmp_path."""

    domains: Path
    handlers: Path


def _make_tree(
    tmp_path: Path,
    domains_files: dict[str, str],
    handlers_files: dict[str, str],
) -> _Tree:
    """Materialise a ``domains/`` and ``handlers/`` tree under *tmp_path*."""
    domains_root = tmp_path / "domains"
    handlers_root = tmp_path / "handlers"
    domains_root.mkdir()
    handlers_root.mkdir()
    for name, body in domains_files.items():
        target = domains_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for name, body in handlers_files.items():
        target = handlers_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return _Tree(domains=domains_root, handlers=handlers_root)


def _run(tree: _Tree) -> list[_ViolationLike]:
    return _MODULE._run_gate(tree.domains, tree.handlers)


# ── Reusable domain shapes ────────────────────────────────────────


_DOMAIN_WITH_MODEL = """\
from synthorg.meta.mcp.tool_builder import read_tool

WIDGET_TOOLS = (
    read_tool("widget", "get", "Get a widget.", args_model=WidgetGetArgs),
)
"""


# ── Tests ─────────────────────────────────────────────────────────


def test_clean_typed_args_handler_passes(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = typed_args(arguments, WidgetGetArgs).widget_id
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    assert _run(tree) == []


def test_raw_arguments_get_is_flagged(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = arguments.get("widget_id")
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_widget_get" in violations[0].message


def test_arguments_subscript_is_flagged(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = arguments["widget_id"]
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    assert len(_run(tree)) == 1


def test_pass_through_to_helper_is_flagged(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = require_arg(arguments, "widget_id", str)
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    assert len(_run(tree)) == 1


def test_inline_opt_out_marker_suppresses(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


async def _widget_get(  # lint-allow: handler-arguments-get -- cataloged mismatch
    *, app_state, arguments, actor=None
):
    widget_id = arguments.get("widget_id")
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    assert _run(tree) == []


def test_multiline_comment_marker_above_def_suppresses(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


# lint-allow: handler-arguments-get -- cataloged mismatch: the wire schema and
# the model disagree on the lookup key; needs a batched contract decision before
# this handler can narrow via typed_args.
async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = arguments.get("widget_id")
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    assert _run(tree) == []


def test_admin_guardrails_plus_typed_args_passes(tmp_path: Path) -> None:
    domain = """\
from synthorg.meta.mcp.tool_builder import admin_tool

WIDGET_TOOLS = (
    admin_tool("widget", "delete", "Delete a widget.", args_model=WidgetDeleteArgs),
)
"""
    handler = """\
from types import MappingProxyType


async def _widget_delete(*, app_state, arguments, actor=None):
    require_admin_guardrails(arguments, actor)
    widget_id = typed_args(arguments, WidgetDeleteArgs).widget_id
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_delete": _widget_delete})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": domain},
        {"widget.py": handler},
    )
    assert _run(tree) == []


def test_factory_built_closure_is_skipped(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType


def _make_widget_handler(kind):
    async def _handler(*, app_state, arguments, actor=None):
        return ok(arguments.get(kind))
    return _handler


WIDGET_HANDLERS = MappingProxyType(
    {"synthorg_widget_get": _make_widget_handler("widget_id")},
)
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    # The gate cannot inspect a closure body, so the factory-built handler is
    # skipped rather than flagged.
    assert _run(tree) == []


def test_name_constant_dict_key_resolves(tmp_path: Path) -> None:
    handler = """\
from types import MappingProxyType

_TOOL_GET = "synthorg_widget_get"


async def _widget_get(*, app_state, arguments, actor=None):
    return ok(arguments.get("widget_id"))


WIDGET_HANDLERS = MappingProxyType({_TOOL_GET: _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": handler},
    )
    # The dict key is a module-level constant; the gate resolves it and still
    # flags the raw-arguments access.
    assert len(_run(tree)) == 1


def test_no_args_model_handler_is_not_checked(tmp_path: Path) -> None:
    domain = """\
from synthorg.meta.mcp.tool_builder import read_tool

WIDGET_TOOLS = (read_tool("widget", "get", "Get a widget."),)
"""
    handler = """\
from types import MappingProxyType


async def _widget_get(*, app_state, arguments, actor=None):
    widget_id = arguments.get("widget_id")
    return ok(widget_id)


WIDGET_HANDLERS = MappingProxyType({"synthorg_widget_get": _widget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": domain},
        {"widget.py": handler},
    )
    # No args_model wired -> the handler keeps its own validation, untouched.
    assert _run(tree) == []


def test_cross_subdirectory_handler_resolves(tmp_path: Path) -> None:
    domain = """\
from synthorg.meta.mcp.tool_builder import read_tool

GADGET_TOOLS = (
    read_tool("gadget", "get", "Get a gadget.", args_model=GadgetGetArgs),
)
"""
    handler = """\
from types import MappingProxyType


async def _gadget_get(*, app_state, arguments, actor=None):
    return ok(arguments.get("gadget_id"))


GADGET_HANDLERS = MappingProxyType({"synthorg_gadget_get": _gadget_get})
"""
    tree = _make_tree(
        tmp_path,
        {"infrastructure/gadget.py": domain},
        {"infrastructure/gadget.py": handler},
    )
    # A handler in a nested sub-package is resolved and checked.
    assert len(_run(tree)) == 1


def test_missing_handler_entry_is_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        {"widget.py": _DOMAIN_WITH_MODEL},
        {"widget.py": "WIDGET_HANDLERS = {}\n"},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "no entry" in violations[0].message


def test_non_literal_domain_is_flagged(tmp_path: Path) -> None:
    domain = """\
from synthorg.meta.mcp.tool_builder import read_tool

_D = "widget"
WIDGET_TOOLS = (read_tool(_D, "get", "Get.", args_model=WidgetGetArgs),)
"""
    tree = _make_tree(
        tmp_path,
        {"widget.py": domain},
        {"widget.py": "WIDGET_HANDLERS = {}\n"},
    )
    violations = _run(tree)
    assert any("literal domain" in v.message for v in violations)
