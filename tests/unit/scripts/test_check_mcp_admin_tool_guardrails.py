"""Tests for the MCP admin_tool guardrail AST gate."""

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
    """Subset of ``scripts/check_mcp_admin_tool_guardrails.py`` the tests exercise."""

    @staticmethod
    def _run_gate(
        domains_root: Path,
        handlers_root: Path,
    ) -> list[_ViolationLike]: ...

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_module() -> _GateModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_mcp_admin_tool_guardrails.py"
    spec = importlib.util.spec_from_file_location(
        "check_mcp_admin_tool_guardrails",
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
        (domains_root / name).write_text(body, encoding="utf-8")
    for name, body in handlers_files.items():
        (handlers_root / name).write_text(body, encoding="utf-8")
    return _Tree(domains=domains_root, handlers=handlers_root)


def _run(tree: _Tree) -> list[_ViolationLike]:
    return _MODULE._run_gate(tree.domains, tree.handlers)


# ── Reusable shapes ───────────────────────────────────────────────


_HANDLER_OK = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    tool = "synthorg_settings_update"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        await app_state.do_thing(reason, resolved_actor)
    except Exception as exc:
        return err(exc)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OK_NO_TRY = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_MISSING_CALL = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    tool = "synthorg_settings_update"
    try:
        await app_state.do_thing()
    except Exception as exc:
        return err(exc)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OTHER_FIRST_CALL = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    tool = "synthorg_settings_update"
    try:
        key = _require_str(arguments, "key")
        require_admin_guardrails(arguments, actor)
    except Exception as exc:
        return err(exc)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_COMPUTED_ARGS = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    try:
        require_admin_guardrails(get_args(), actor)
    except Exception as exc:
        return err(exc)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OPT_OUT = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(  # lint-allow: mcp-admin-guardrail -- dup upstream
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OPT_OUT_NO_JUSTIFICATION = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(  # lint-allow: mcp-admin-guardrail --
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OPT_OUT_NO_DOUBLE_DASH = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(  # lint-allow: mcp-admin-guardrail
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OPT_OUT_CLOSING_LINE = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:  # lint-allow: mcp-admin-guardrail -- header span end-of-line
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_OPT_OUT_CLOSING_LINE_NO_JUSTIFICATION = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:  # lint-allow: mcp-admin-guardrail --
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""


_HANDLER_NON_ADMIN_MISSING = """\
from types import MappingProxyType
from typing import Any


async def _settings_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    rows = await app_state.list_things()
    return ok(rows)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_list": _settings_list,
    },
)
"""


def _domain_admin(domain: str, action: str) -> str:
    return (
        "from synthorg.meta.mcp.tool_builder import admin_tool, read_tool\n"
        "\n"
        "TOOLS = (\n"
        f"    admin_tool({domain!r}, {action!r}, 'desc'),\n"
        ")\n"
    )


def _domain_read(domain: str, action: str) -> str:
    return (
        "from synthorg.meta.mcp.tool_builder import admin_tool, read_tool\n"
        "\n"
        "TOOLS = (\n"
        f"    read_tool({domain!r}, {action!r}, 'desc'),\n"
        ")\n"
    )


# ── positive cases (must not flag) ────────────────────────────────


def test_canonical_try_block_shape_passes(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OK},
    )
    assert _run(tree) == []


def test_call_as_first_body_statement_passes(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OK_NO_TRY},
    )
    assert _run(tree) == []


def test_opt_out_marker_with_justification_passes(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OPT_OUT},
    )
    assert _run(tree) == []


def test_opt_out_marker_on_closing_signature_line_passes(tmp_path: Path) -> None:
    """Marker on the closing ``) -> ...:`` line of a multi-line header passes.

    The gate scans every physical line of the function header span (from
    ``async def`` through the line that starts the body), so a marker
    that ruff-format pushed onto the closing signature line still
    satisfies the opt-out grammar.
    """
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OPT_OUT_CLOSING_LINE},
    )
    assert _run(tree) == []


def test_non_admin_handler_without_call_passes(tmp_path: Path) -> None:
    """The gate scopes only to admin tools; read tools are out of scope."""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_read("settings", "list")},
        handlers_files={"settings.py": _HANDLER_NON_ADMIN_MISSING},
    )
    assert _run(tree) == []


# ── negative cases (must flag) ────────────────────────────────────


def test_missing_call_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_MISSING_CALL},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_update" in violations[0].message
    assert "require_admin_guardrails" in violations[0].message
    assert violations[0].rel_path.endswith("handlers/settings.py")


def test_other_call_before_guardrail_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OTHER_FIRST_CALL},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_update" in violations[0].message


def test_computed_first_arg_flagged(tmp_path: Path) -> None:
    """``require_admin_guardrails(get_args(), actor)`` must be rejected.

    The contract is a literal-args call (bare ``arguments`` and bare
    ``actor`` Names). A computed first arg defeats the audit identity
    we rely on.
    """
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_COMPUTED_ARGS},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "require_admin_guardrails" in violations[0].message


def test_opt_out_without_justification_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OPT_OUT_NO_JUSTIFICATION},
    )
    violations = _run(tree)
    assert len(violations) == 1


def test_opt_out_without_double_dash_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": _HANDLER_OPT_OUT_NO_DOUBLE_DASH},
    )
    violations = _run(tree)
    assert len(violations) == 1


def test_opt_out_marker_on_closing_line_without_justification_flagged(
    tmp_path: Path,
) -> None:
    """Marker on the closing line still requires the full opt-out grammar.

    Pairs with ``test_opt_out_marker_on_closing_signature_line_passes``: the
    multi-line header span does NOT relax the justification requirement.
    A marker that lacks a non-empty reason on the closing ``) -> ...:``
    line is flagged exactly as it would be on the ``async def`` line.
    """
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={
            "settings.py": _HANDLER_OPT_OUT_CLOSING_LINE_NO_JUSTIFICATION,
        },
    )
    violations = _run(tree)
    assert len(violations) == 1


def test_admin_tool_with_variable_domain_is_hard_error(tmp_path: Path) -> None:
    bad_domain = (
        "from synthorg.meta.mcp.tool_builder import admin_tool\n"
        "_DOMAIN = 'settings'\n"
        "TOOLS = (\n"
        "    admin_tool(_DOMAIN, 'update', 'desc'),\n"
        ")\n"
    )
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": bad_domain},
        handlers_files={"settings.py": _HANDLER_OK},
    )
    violations = _run(tree)
    assert any("literal" in v.message for v in violations)


def test_admin_tool_imported_via_alias_discovered(tmp_path: Path) -> None:
    """Aliased ``from ... import admin_tool as <alias>`` still resolves.

    Pass 1 used to match only ``ast.Name(id="admin_tool")``, so an
    aliased call would slip through and the handler for that key would
    never be guardrail-checked. The alias collector + extended matcher
    discovers the same key under any local rebinding.
    """
    aliased_domain = (
        "from synthorg.meta.mcp.tool_builder import admin_tool as at\n"
        "TOOLS = (\n"
        "    at('settings', 'update', 'desc'),\n"
        ")\n"
    )
    handlers_missing = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": aliased_domain},
        handlers_files={"settings.py": handlers_missing},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_update" in violations[0].message
    assert "require_admin_guardrails" in violations[0].message


def test_attribute_mutation_before_guardrail_flagged(tmp_path: Path) -> None:
    """``self.x = 1`` / ``arguments[\"y\"] = 1`` before the guardrail flags.

    ``_is_trivial_prelude_stmt`` previously skipped any assignment whose
    RHS was a constant -- ``app_state.flag = True`` looked trivial even
    though it mutates external state. The hardened version requires the
    target to be a bare local name; assignments to attributes /
    subscripts are real statements and the guardrail must precede them.
    """
    handlers_body = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    app_state.flag = True
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": handlers_body},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_update" in violations[0].message


def test_subscript_mutation_before_guardrail_flagged(tmp_path: Path) -> None:
    """``arguments[\"x\"] = ...`` before the guardrail also flags."""
    handlers_body = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    arguments["confirm"] = True
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": handlers_body},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_update" in violations[0].message


def test_duplicate_handler_key_in_two_maps_flagged(tmp_path: Path) -> None:
    """The same admin key in two ``*_HANDLERS`` maps fails closed.

    A guarded decoy entry can hide an unguarded real handler for the
    same key; ``_find_dict_value_for_key`` used to return the first
    match and stop. The hardened version collects every match and
    refuses to disambiguate when there are two.
    """
    domain = (
        "from synthorg.meta.mcp.tool_builder import admin_tool\n"
        "TOOLS = (admin_tool('settings', 'update', 'desc'),)\n"
    )
    handlers_a = """\
from types import MappingProxyType
from typing import Any


async def _settings_update_guarded(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update_guarded,
    },
)
"""
    handlers_b = """\
from types import MappingProxyType
from typing import Any


async def _settings_update_unguarded(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


OTHER_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update_unguarded,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": domain},
        handlers_files={"a.py": handlers_a, "b.py": handlers_b},
    )
    violations = _run(tree)
    assert any("multiple *_HANDLERS maps" in v.message for v in violations)


def test_handlers_dict_with_non_literal_key_unfindable(tmp_path: Path) -> None:
    """A non-literal key hides the handler so the admin key has no entry.

    The gate doesn't try to resolve ``_KEY`` to its constant binding;
    a non-literal key is silently dropped from the dict snapshot, which
    leaves the admin tool with no resolvable entry. The resolver flags
    that as a "no entry in any *_HANDLERS map" violation -- which is
    the right backstop: a future refactor that hides a handler behind
    indirection can't slip past the gate.
    """
    handlers_body = """\
from types import MappingProxyType
from typing import Any

_KEY = "synthorg_settings_update"


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        _KEY: _settings_update,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": handlers_body},
    )
    violations = _run(tree)
    assert any("no entry" in v.message.lower() for v in violations)


def test_admin_tool_without_handler_flagged(tmp_path: Path) -> None:
    """A registered admin_tool with no matching *_HANDLERS entry must flag."""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={
            "settings.py": (
                "from types import MappingProxyType\n"
                "SETTINGS_HANDLERS = MappingProxyType({})\n"
            ),
        },
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "synthorg_settings_update" in violations[0].message


def test_unparseable_handlers_file_flagged(tmp_path: Path) -> None:
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": "def broken(:\n    pass\n"},
    )
    violations = _run(tree)
    assert any("failed to parse" in v.message for v in violations)


def test_unparseable_domains_file_flagged(tmp_path: Path) -> None:
    """A SyntaxError on the domains/ side fails closed, same as handlers/.

    Pairs with ``test_unparseable_handlers_file_flagged``: the
    ``_discover_admin_keys`` walker must catch ``InspectionError`` and
    emit a ``failed to parse`` violation rather than letting the
    exception escape and bypassing every later admin-tool check.
    """
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": "TOOLS = (admin_tool(\n"},
        handlers_files={"settings.py": _HANDLER_OK},
    )
    violations = _run(tree)
    assert any("failed to parse" in v.message for v in violations)


# ── multi-tool / multi-file ──────────────────────────────────────


def test_multiple_admin_tools_one_missing(tmp_path: Path) -> None:
    """Two admin tools registered; only the second is missing the call."""
    domain_body = (
        "from synthorg.meta.mcp.tool_builder import admin_tool\n"
        "TOOLS = (\n"
        "    admin_tool('settings', 'update', 'desc'),\n"
        "    admin_tool('settings', 'reset', 'desc'),\n"
        ")\n"
    )
    handlers_body = """\
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)


async def _settings_reset(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _settings_update,
        "synthorg_settings_reset": _settings_reset,
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": domain_body},
        handlers_files={"settings.py": handlers_body},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "_settings_reset" in violations[0].message


def test_admin_tool_keyword_args_resolved(tmp_path: Path) -> None:
    """``admin_tool(domain="settings", action="update", ...)`` must work."""
    domain_body = (
        "from synthorg.meta.mcp.tool_builder import admin_tool\n"
        "TOOLS = (\n"
        "    admin_tool(domain='settings', action='update', description='desc'),\n"
        ")\n"
    )
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": domain_body},
        handlers_files={"settings.py": _HANDLER_OK},
    )
    assert _run(tree) == []


# ── deepcopy wrapping + cross-module aliases ─────────────────────


def test_deepcopy_wrapped_dict_resolves(tmp_path: Path) -> None:
    """``MappingProxyType(copy.deepcopy({...}))`` is the same shape as a bare dict."""
    handlers_body = """\
import copy
from types import MappingProxyType
from typing import Any


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)


SETTINGS_HANDLERS = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_settings_update": _settings_update,
        },
    ),
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": handlers_body},
    )
    assert _run(tree) == []


def test_cross_module_alias_resolves_via_import(tmp_path: Path) -> None:
    """``_alias = imported_name`` followed across modules, then to a def."""
    aggregator = """\
from types import MappingProxyType
from typing import Any
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_cancel as _cancel_impl,
)

_workflow_executions_cancel = _cancel_impl


WORKFLOW_HANDLERS = MappingProxyType(
    {
        "synthorg_workflow_executions_cancel": _workflow_executions_cancel,
    },
)
"""
    leaf = """\
from typing import Any


async def workflow_executions_cancel(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={
            "workflows.py": _domain_admin("workflow_executions", "cancel"),
        },
        handlers_files={
            "workflows.py": aggregator,
            "workflow_executions.py": leaf,
        },
    )
    assert _run(tree) == []


def test_cross_module_alias_resolves_via_relative_import(tmp_path: Path) -> None:
    """``from .other import ...`` resolves the same way as the absolute form.

    Pairs with ``test_cross_module_alias_resolves_via_import``: the
    relative-import grammar is the second canonical shape for the
    aggregator -> leaf alias chain. _collect_imports normalises
    ``from .X import y`` against the importing file's dotted path
    (``synthorg.meta.mcp.handlers.workflows`` here) into the absolute
    target ``synthorg.meta.mcp.handlers.workflow_executions`` so the
    resolver follows the alias the same way as the absolute case.
    """
    aggregator = """\
from types import MappingProxyType
from typing import Any
from .workflow_executions import workflow_executions_cancel as _cancel_impl

_workflow_executions_cancel = _cancel_impl


WORKFLOW_HANDLERS = MappingProxyType(
    {
        "synthorg_workflow_executions_cancel": _workflow_executions_cancel,
    },
)
"""
    leaf = """\
from typing import Any


async def workflow_executions_cancel(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: Any | None = None,
) -> str:
    require_admin_guardrails(arguments, actor)
    return ok(None)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={
            "workflows.py": _domain_admin("workflow_executions", "cancel"),
        },
        handlers_files={
            "workflows.py": aggregator,
            "workflow_executions.py": leaf,
        },
    )
    assert _run(tree) == []


def test_factory_built_handler_flagged(tmp_path: Path) -> None:
    """A factory-call value (closure) is flagged unless explicitly opted out.

    The gate cannot statically inspect a closure body. It surfaces the
    site so the operator either (a) refactors to bind a named def, or
    (b) annotates the def line once the def is reachable.
    """
    handlers_body = """\
from types import MappingProxyType
from typing import Any


def _make_handler(method: str):
    async def _impl(
        *,
        app_state: Any,
        arguments: dict[str, Any],
        actor: Any | None = None,
    ) -> str:
        return ok(method)
    return _impl


SETTINGS_HANDLERS = MappingProxyType(
    {
        "synthorg_settings_update": _make_handler(method="update"),
    },
)
"""
    tree = _make_tree(
        tmp_path,
        domains_files={"settings.py": _domain_admin("settings", "update")},
        handlers_files={"settings.py": handlers_body},
    )
    violations = _run(tree)
    assert len(violations) == 1
    assert "factory" in violations[0].message.lower()


# ── CLI smoke ─────────────────────────────────────────────────────


def test_main_returns_zero_on_clean_real_tree() -> None:
    """The real repo tree must be clean: every admin handler either
    calls ``require_admin_guardrails(arguments, actor)`` as the
    lexically first call OR carries an annotated opt-out marker.
    """
    rc = _MODULE.main([])
    assert rc == 0
