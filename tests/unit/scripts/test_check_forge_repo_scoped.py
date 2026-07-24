"""Tests for the forge repo-scope enforcement gate.

The gate is behavioural, not a token search, so the cases below are
mutations of the real shape: a scope error that is only *mentioned*, a
rejection parked after an unconditional return, and an override that
neither delegates nor re-enforces must all fail.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORGE_REL = "src/synthorg/tools/forge"


class _GateModule(Protocol):
    """Subset of ``scripts/check_forge_repo_scoped.py`` the tests exercise."""

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_forge_repo_scoped.py"
    spec = importlib.util.spec_from_file_location(
        "check_forge_repo_scoped",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Materialise a synthetic ``tools/forge`` package under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    forge_dir = tmp_path / _FORGE_REL
    forge_dir.mkdir(parents=True)
    for name, body in files.items():
        (forge_dir / name).write_text(body, encoding="utf-8")
    return tmp_path


_BASE_OK = """\
class _BaseForgeTool(GovernedConnectionTool):
    async def _resolve_connection(self, args):
        conn = await super()._resolve_connection(args)
        if not _repo_in_scope(args.owner, args.repo, conn.allowed_repos):
            raise ForgeRepoScopeError("out of scope")
        return conn
"""


_BASE_MENTION_ONLY = """\
class _BaseForgeTool(GovernedConnectionTool):
    async def _resolve_connection(self, args):
        try:
            return await super()._resolve_connection(args)
        except ForgeRepoScopeError:
            return None
"""


_BASE_DEAD_RAISE = """\
class _BaseForgeTool(GovernedConnectionTool):
    async def _resolve_connection(self, args):
        conn = await super()._resolve_connection(args)
        return conn
        raise ForgeRepoScopeError("never reached")
"""


def test_canonical_base_enforcement_passes(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK})
    assert _MODULE._check(root) == []


def test_base_that_only_mentions_the_error_flagged(tmp_path: Path) -> None:
    """An ``except ForgeRepoScopeError`` handler is not enforcement.

    It swallows the very error the scope check raises, so a token search
    would pass while every repository stayed admitted.
    """
    root = _make_tree(tmp_path, {"_base.py": _BASE_MENTION_ONLY})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "reachable raise" in violations[0]


def test_base_with_dead_raise_flagged(tmp_path: Path) -> None:
    """A rejection after an unconditional ``return`` never runs."""
    root = _make_tree(tmp_path, {"_base.py": _BASE_DEAD_RAISE})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "reachable raise" in violations[0]


def test_missing_base_override_flagged(tmp_path: Path) -> None:
    root = _make_tree(
        tmp_path,
        {"_base.py": "class _BaseForgeTool(GovernedConnectionTool):\n    pass\n"},
    )
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "_BaseForgeTool" in violations[0]


def test_subclass_bypass_flagged(tmp_path: Path) -> None:
    """An override that neither delegates nor re-enforces is a bypass."""
    bypass = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        return await self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": bypass})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "ForgePushTool" in violations[0]


def test_subclass_delegating_to_super_passes(tmp_path: Path) -> None:
    delegating = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        conn = await super()._resolve_connection(args)
        return conn
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": delegating})
    assert _MODULE._check(root) == []


def test_subclass_with_dead_super_delegation_flagged(tmp_path: Path) -> None:
    """Delegation parked after an early return is not delegation."""
    dead = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        return await self._catalog.get(self._connection)
        return await super()._resolve_connection(args)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": dead})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "ForgePushTool" in violations[0]


def test_subclass_reinforcing_itself_passes(tmp_path: Path) -> None:
    reinforcing = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        conn = await self._catalog.get(self._connection)
        if not _repo_in_scope(args.owner, args.repo, conn.allowed_repos):
            raise ForgeRepoScopeError("out of scope")
        return conn
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": reinforcing})
    assert _MODULE._check(root) == []


def test_sync_override_is_also_checked(tmp_path: Path) -> None:
    """A synchronous override bypasses the scope check just as well."""
    sync_bypass = """\
class ForgePushTool(_BaseForgeTool):
    def _resolve_connection(self, args):
        return self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": sync_bypass})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "ForgePushTool" in violations[0]


def test_nested_module_is_scanned(tmp_path: Path) -> None:
    """A bypass in a subpackage is still a bypass."""
    bypass = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        return await self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK})
    nested = root / _FORGE_REL / "sub"
    nested.mkdir()
    (nested / "tools.py").write_text(bypass, encoding="utf-8")
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "sub/tools.py" in violations[0]


def test_enforcement_inside_a_nested_helper_does_not_count(tmp_path: Path) -> None:
    """A raise inside an inner function is that function's control flow.

    The enclosing override returns without ever calling it, so the scope
    check never runs even though the raise is lexically present.
    """
    nested_helper = """\
class ForgePushTool(_BaseForgeTool):
    async def _resolve_connection(self, args):
        def _unused():
            raise ForgeRepoScopeError("never called")

        return await self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": nested_helper})
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "ForgePushTool" in violations[0]


def test_annotated_opt_out_marker_passes(tmp_path: Path) -> None:
    opted_out = """\
class ForgePushTool(_BaseForgeTool):  # lint-allow: forge-repo-scoped -- no repo arg
    async def _resolve_connection(self, args):
        return await self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": opted_out})
    assert _MODULE._check(root) == []


def test_opt_out_marker_without_justification_flagged(tmp_path: Path) -> None:
    bare_marker = """\
class ForgePushTool(_BaseForgeTool):  # lint-allow: forge-repo-scoped
    async def _resolve_connection(self, args):
        return await self._catalog.get(self._connection)
"""
    root = _make_tree(tmp_path, {"_base.py": _BASE_OK, "forge_tools.py": bare_marker})
    assert len(_MODULE._check(root)) == 1


def test_real_tree_is_clean() -> None:
    assert _MODULE._check(_REPO_ROOT) == []
