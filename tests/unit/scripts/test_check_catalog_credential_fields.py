"""Tests for the catalog credential-field gate.

The two sides of the contract live in different files, so the gate is the
only thing that notices when an entry maps a field its connection type does
not store: injection is an exact-name lookup, and a miss launches the server
unauthenticated.
"""

import importlib.util
import json
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOG_REL = "src/synthorg/integrations/mcp_catalog/bundled.json"


class _GateModule(Protocol):
    """Subset of ``scripts/check_catalog_credential_fields.py`` under test."""

    @staticmethod
    def _check(repo_root: Path) -> list[str]: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_catalog_credential_fields.py"
    spec = importlib.util.spec_from_file_location(
        "check_catalog_credential_fields",
        script_path,
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _make_tree(tmp_path: Path, servers: list[dict[str, object]]) -> Path:
    """Materialise a synthetic catalog under *tmp_path*.

    Returns:
        The synthetic repository root.
    """
    path = tmp_path / _CATALOG_REL
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return tmp_path


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "example-mcp",
        "name": "Example",
        "required_connection_type": "generic_http",
        "credential_env_map": {"token": "EXAMPLE_API_KEY"},
    }
    entry.update(overrides)
    return entry


class TestGate:
    def test_declared_field_passes(self, tmp_path: Path) -> None:
        root = _make_tree(tmp_path, [_entry()])

        assert _MODULE._check(root) == []

    def test_undeclared_field_is_reported(self, tmp_path: Path) -> None:
        root = _make_tree(
            tmp_path,
            [_entry(credential_env_map={"api_key": "EXAMPLE_API_KEY"})],
        )

        violations = _MODULE._check(root)

        assert len(violations) == 1
        assert "api_key" in violations[0]
        assert "unauthenticated" in violations[0]

    def test_every_undeclared_field_is_reported(self, tmp_path: Path) -> None:
        root = _make_tree(
            tmp_path,
            [_entry(credential_env_map={"api_key": "A", "secret": "B"})],
        )

        assert len(_MODULE._check(root)) == 2

    def test_connectionless_entry_is_skipped(self, tmp_path: Path) -> None:
        root = _make_tree(
            tmp_path,
            [_entry(required_connection_type=None, credential_env_map={})],
        )

        assert _MODULE._check(root) == []

    def test_shipped_catalog_is_clean(self) -> None:
        assert _MODULE._check(_REPO_ROOT) == []
