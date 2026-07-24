"""Tests for the MCP npm version-pin validator gate.

The validator's presence is not the contract: it has to inspect the
launch command, decide, and reject. Each mutation below keeps the name
and the decorator while hollowing out one of those three.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_REL = "src/synthorg/tools/mcp/config.py"


class _GateModule(Protocol):
    """Subset of ``scripts/check_mcp_server_config_pinned.py`` under test."""

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_mcp_server_config_pinned.py"
    spec = importlib.util.spec_from_file_location(
        "check_mcp_server_config_pinned",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_tree(tmp_path: Path, validator_body: str) -> Path:
    """Materialise a synthetic ``MCPServerConfig`` module under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    path = tmp_path / _CONFIG_REL
    path.parent.mkdir(parents=True)
    path.write_text(
        "class MCPServerConfig(BaseModel):\n" + validator_body,
        encoding="utf-8",
    )
    return tmp_path


_OK = """\
    @model_validator(mode="after")
    def _validate_npm_pin(self):
        if self.transport != "stdio" or self.command is None:
            return self
        spec = _npm_package_spec(str(self.command), self.args)
        if spec is None or _npm_spec_is_pinned(spec):
            return self
        raise ValueError("unpinned")
"""


def test_canonical_validator_passes(tmp_path: Path) -> None:
    assert _MODULE._check(_make_tree(tmp_path, _OK)) == []


def test_missing_validator_flagged(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, "    pass\n")
    violations = _MODULE._check(root)
    assert len(violations) == 1
    assert "_validate_npm_pin" in violations[0]


def test_validator_without_decorator_flagged(tmp_path: Path) -> None:
    """An undecorated method never runs, so pydantic never enforces it."""
    body = """\
    def _validate_npm_pin(self):
        if self.command is None:
            return self
        if not _npm_spec_is_pinned(self.args):
            raise ValueError("unpinned")
        return self
"""
    assert len(_MODULE._check(_make_tree(tmp_path, body))) == 1


def test_no_op_validator_flagged(tmp_path: Path) -> None:
    """``return self`` keeps the name and the decorator and enforces nothing."""
    body = """\
    @model_validator(mode="after")
    def _validate_npm_pin(self):
        return self
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 3
    joined = " ".join(violations)
    assert "self.command" in joined
    assert "returns unconditionally" in joined
    assert "never rejected" in joined


def test_validator_with_dead_rejection_flagged(tmp_path: Path) -> None:
    """A rejection after an unconditional return never fires."""
    body = """\
    @model_validator(mode="after")
    def _validate_npm_pin(self):
        if self.transport != "stdio":
            return self
        spec = _npm_package_spec(str(self.command), self.args)
        return self
        raise ValueError("unpinned")
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 1
    assert "never rejected" in violations[0]


def test_validator_ignoring_args_flagged(tmp_path: Path) -> None:
    """The package spec rides in ``args`` (``npx -y pkg@1.2.3``).

    A validator that only looks at ``command`` sees ``npx`` and nothing
    else, so every package it is meant to pin is invisible to it.
    """
    body = """\
    @model_validator(mode="after")
    def _validate_npm_pin(self):
        if self.command is None:
            return self
        if not _npm_spec_is_pinned(str(self.command)):
            raise ValueError("unpinned")
        return self
"""
    violations = _MODULE._check(_make_tree(tmp_path, body))
    assert len(violations) == 1
    assert "self.args" in violations[0]


def test_real_tree_is_clean() -> None:
    assert _MODULE._check(_REPO_ROOT) == []
