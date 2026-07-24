"""Tests for the MCP self-consumer per-agent scoping gate.

Reading ``identity.tools.mcp_capabilities`` is not enough: the grant has
to reach the capability set the scoper selects visible tools from. The
mutations below keep the read and break the wiring.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SELF_CONSUMER_REL = "src/synthorg/engine/mcp_self_consumer.py"


class _GateModule(Protocol):
    """Subset of ``scripts/check_mcp_self_consumer_scoped.py`` under test."""

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_mcp_self_consumer_scoped.py"
    spec = importlib.util.spec_from_file_location(
        "check_mcp_self_consumer_scoped",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_tree(tmp_path: Path, body: str) -> Path:
    """Materialise a synthetic self-consumer module under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    path = tmp_path / _SELF_CONSUMER_REL
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    return tmp_path


def test_canonical_wiring_passes(tmp_path: Path) -> None:
    body = """\
def build(config, scoper):
    def _provide(identity, access_level):
        capabilities = tuple(
            {*identity.tools.mcp_capabilities, *config.elevated_capabilities}
        )
        return scoper.visible_tools(capabilities, allowed=(), denied=())

    return _provide
"""
    assert _MODULE._check(_make_tree(tmp_path, body)) == []


def test_keyword_capability_argument_passes(tmp_path: Path) -> None:
    body = """\
def build(config, scoper):
    def _provide(identity, access_level):
        granted = identity.tools.mcp_capabilities
        return scoper.visible_tools(capabilities=granted, allowed=())

    return _provide
"""
    assert _MODULE._check(_make_tree(tmp_path, body)) == []


def test_grant_read_but_discarded_flagged(tmp_path: Path) -> None:
    """Logging the grant while scoping from a global list is a regression.

    The attribute chain is still present, so a token search passes, but
    every ELEVATED agent sees the same org-wide capability set.
    """
    body = """\
def build(config, scoper):
    def _provide(identity, access_level):
        logger.debug("grant", granted=identity.tools.mcp_capabilities)
        return scoper.visible_tools(config.elevated_capabilities, allowed=())

    return _provide
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 1
    assert "never reaches" in violations[0]


def test_grant_not_read_at_all_flagged(tmp_path: Path) -> None:
    body = """\
def build(config, scoper):
    def _provide(identity, access_level):
        return scoper.visible_tools(config.elevated_capabilities, allowed=())

    return _provide
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 1
    assert "must read" in violations[0]


def test_grant_in_a_different_function_flagged(tmp_path: Path) -> None:
    """The grant must feed the scoper call, not merely share a module."""
    body = """\
def describe(identity):
    return identity.tools.mcp_capabilities


def build(config, scoper):
    def _provide(identity, access_level):
        return scoper.visible_tools(config.elevated_capabilities, allowed=())

    return _provide
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 1
    assert "never reaches" in violations[0]


def test_real_tree_is_clean() -> None:
    assert _MODULE._check(_REPO_ROOT) == []
