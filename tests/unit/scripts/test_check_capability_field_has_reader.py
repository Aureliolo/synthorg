"""Unit tests for ``scripts/check_capability_field_has_reader.py``.

The gate exists because six ``ModelCapabilities`` fields shipped with
no reader outside the class's own validator, hidden from every existing
gate because identically-named fields on other DTOs absorbed a naive
tree-wide attribute-access scan. The tests below are built around that
failure mode: a field read only through an unrelated object of the same
attribute name must still be reported as unread, because the scan is
scoped to modules that reference the class at all.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_declared_event_is_emitted.py``.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var so the gate's ``git ls-files`` subprocess
    cannot escape the ``tmp_path`` sandbox and read the live repo.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_capability_field_has_reader.py"

_CAPABILITIES_MODULE_REL = "src/synthorg/providers/capabilities.py"
_CAPABILITIES_SOURCE = (
    "from pydantic import BaseModel\n\n\n"
    "class ModelCapabilities(BaseModel):\n"
    "    model_id: str\n"
    "    provider: str\n"
    "    supports_tools: bool = False\n"
)


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    name: str
    lineno: int

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _scan(project_root: Path) -> list[_HitView]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_capability_field_has_reader",
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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_repo(
    tmp_path: Path,
    *,
    capabilities_source: str = _CAPABILITIES_SOURCE,
) -> Path:
    """Build a minimal git-tracked tree carrying ``ModelCapabilities``."""
    _write(tmp_path / _CAPABILITIES_MODULE_REL, capabilities_source)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _commit(tmp_path: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-q", "-m", "wip"],  # noqa: S607
        cwd=tmp_path,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com"},
    )


def test_field_read_by_a_type_referencing_module_is_not_a_hit(
    tmp_path: Path,
) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "from synthorg.providers.capabilities import ModelCapabilities\n\n"
        "def run(caps: ModelCapabilities) -> bool:\n"
        "    return caps.supports_tools\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "supports_tools" not in names


def test_field_read_only_by_its_own_validator_is_a_hit(tmp_path: Path) -> None:
    """The exact shape all six retired fields had."""
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel, model_validator\n\n\n"
            "class ModelCapabilities(BaseModel):\n"
            "    model_id: str\n"
            "    provider: str\n"
            "    max_output_tokens: int\n"
            "    max_context_tokens: int\n\n"
            "    @model_validator(mode='after')\n"
            "    def _check(self):\n"
            "        if self.max_output_tokens > self.max_context_tokens:\n"
            "            raise ValueError('too big')\n"
            "        return self\n"
        ),
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "max_output_tokens" in names
    assert "max_context_tokens" in names


def test_identically_named_field_on_an_unrelated_type_does_not_clear_it(
    tmp_path: Path,
) -> None:
    """The false positive that hid the defect from every prior gate.

    A module reading ``config.cost_per_1k_input`` off a DIFFERENT type must
    not count, because it never references ``ModelCapabilities`` at all.
    """
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel\n\n\n"
            "class ModelCapabilities(BaseModel):\n"
            "    model_id: str\n"
            "    provider: str\n"
            "    cost_per_1k_input: float\n"
        ),
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "class ProviderModelConfig:\n"
        "    cost_per_1k_input: float = 0.0\n\n"
        "def run(config: ProviderModelConfig) -> float:\n"
        "    return config.cost_per_1k_input\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "cost_per_1k_input" in names


def test_the_declaring_modules_own_read_does_not_count(tmp_path: Path) -> None:
    """A validator reading its own field is not a consumer."""
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel, model_validator\n\n\n"
            "class ModelCapabilities(BaseModel):\n"
            "    model_id: str\n"
            "    provider: str\n"
            "    supports_vision: bool = False\n\n"
            "    @model_validator(mode='after')\n"
            "    def _check(self):\n"
            "        _ = self.supports_vision\n"
            "        return self\n"
        ),
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "supports_vision" in names


def test_lint_allow_marker_suppresses_a_violation(tmp_path: Path) -> None:
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel\n\n\n"
            "class ModelCapabilities(BaseModel):\n"
            "    model_id: str\n"
            "    provider: str\n"
            "    supports_embeddings: bool = False  "
            "# lint-allow: capability-field-unread -- read by an external dashboard\n"
        ),
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "supports_embeddings" not in names


def test_lint_allow_marker_without_reason_does_not_suppress(tmp_path: Path) -> None:
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel\n\n\n"
            "class ModelCapabilities(BaseModel):\n"
            "    model_id: str\n"
            "    provider: str\n"
            "    supports_embeddings: bool = False  "
            "# lint-allow: capability-field-unread\n"
        ),
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "supports_embeddings" in names


def test_clean_tree_passes(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "from synthorg.providers.capabilities import ModelCapabilities\n\n"
        "def run(caps: ModelCapabilities) -> bool:\n"
        "    return caps.model_id and caps.provider and caps.supports_tools\n",
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 0


def test_dirty_tree_fails(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 1


def test_missing_class_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path, capabilities_source="X = 1\n")
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_class_with_no_fields_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(
        tmp_path,
        capabilities_source=(
            "from pydantic import BaseModel\n\n\nclass ModelCapabilities(BaseModel):\n"
            "    pass\n"
        ),
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_unparseable_file_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "def broken(:\n",
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2
