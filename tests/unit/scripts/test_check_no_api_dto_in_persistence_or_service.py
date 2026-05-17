"""Unit tests for scripts/check_no_api_dto_in_persistence_or_service.py.

Covers the import-walk logic, the exit-code mapping (0 clean, 1 policy
violation, 2 argv error, 3 I/O error), and a smoke test against the
real persistence tree to assert it stays clean after Track 3.1's
import swap.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_api_dto_in_persistence_or_service.py"


def _load_script_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "_check_no_api_dto",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


@pytest.mark.parametrize(
    "source",
    [
        "from synthorg.api.dto_provider_capabilities import PresetOverride\n",
        (
            "from synthorg.api.dto_provider_capabilities import (\n"
            "    PresetOverride,\n"
            ")\n"
        ),
        "import synthorg.api.dto_provider_capabilities\n",
        "from synthorg.api.dto_other_module import Thing\n",
    ],
)
def test_dto_imports_flagged(tmp_path: Path, source: str) -> None:
    """Every shape of api.dto_* import in a scanned file is flagged."""
    f = tmp_path / "violating.py"
    f.write_text(source, encoding="utf-8")
    rc = _MODULE.main([str(f)])  # type: ignore[attr-defined]
    assert rc == 1


@pytest.mark.parametrize(
    "source",
    [
        "from synthorg.api.controllers.webhooks import WebhookController\n",
        "from synthorg.api.services.idempotency_service import IdempotencyService\n",
        "from synthorg.providers.management.capability_dtos import PresetOverride\n",
        "import json\n",
        "from typing import Protocol\n",
        "",
    ],
)
def test_non_dto_imports_pass(tmp_path: Path, source: str) -> None:
    """Imports from synthorg.api that are not dto_* and unrelated imports pass clean."""
    f = tmp_path / "clean.py"
    f.write_text(source, encoding="utf-8")
    rc = _MODULE.main([str(f)])  # type: ignore[attr-defined]
    assert rc == 0


def test_default_scope_clean() -> None:
    """Empty argv falls back to default discovery; tree must be clean."""
    rc = _MODULE.main([])  # type: ignore[attr-defined]
    assert rc == 0


def test_io_error_returns_3(tmp_path: Path) -> None:
    """A missing file yields exit code 3, not 1."""
    missing = tmp_path / "does_not_exist.py"
    rc = _MODULE.main([str(missing)])  # type: ignore[attr-defined]
    assert rc == 3


def test_syntax_error_returns_3(tmp_path: Path) -> None:
    """A malformed Python source yields exit code 3."""
    f = tmp_path / "broken.py"
    f.write_text("def(:\n", encoding="utf-8")
    rc = _MODULE.main([str(f)])  # type: ignore[attr-defined]
    assert rc == 3


def test_persistence_tree_is_clean() -> None:
    """Smoke test: the real persistence tree must be clean post Track 3.1."""
    persistence_root = _REPO_ROOT / "src" / "synthorg" / "persistence"
    py_files = [str(p) for p in persistence_root.rglob("*.py")]
    assert py_files, "no persistence files found; sanity check failed"
    rc = _MODULE.main(py_files)  # type: ignore[attr-defined]
    assert rc == 0, "persistence tree must not import api.dto_*"
