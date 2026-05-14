"""Shared helpers for the bootstrap-wiring lint test suite.

Test fixtures, the lint-script importer, and the fake-repo builder
live here so the per-category test files (``test_check_setting_to_startup_trace_*.py``)
stay focused and readable. Not collected by pytest -- the
underscore prefix keeps it out of the test-discovery walk.
"""

import importlib.util
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_setting_to_startup_trace.py"


def _load_script_module() -> object:
    """Import the lint script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_setting_to_startup_trace",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_script_module()
"""Public alias for the loaded lint module. Test files access
private helpers (``_load_baseline``, ``_build_class_index``, etc.)
plus public functions and dataclasses through this object."""

REPO_ROOT = _REPO_ROOT
"""Project root for the real-repo smoke test."""


def make_fake_repo(
    tmp_path: Path,
    *,
    settings_files: dict[str, str] | None = None,
    api_files: dict[str, str] | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Create a minimal fake-synthorg tree under ``tmp_path``.

    Returns the project root (``tmp_path`` itself). Files are written
    relative to ``src/synthorg/``. The lint walks this layout exactly
    like the real repo.
    """
    src_root = tmp_path / "src" / "synthorg"
    settings_dir = src_root / "settings" / "definitions"
    settings_dir.mkdir(parents=True)
    api_dir = src_root / "api"
    api_dir.mkdir()
    (src_root / "settings" / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "settings" / "definitions" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (src_root / "settings" / "enums.py").write_text(
        textwrap.dedent("""
        from enum import StrEnum

        class SettingNamespace(StrEnum):
            BACKUP = "backup"
            SECURITY = "security"
            ENGINE = "engine"
            API = "api"
        """).lstrip(),
        encoding="utf-8",
    )
    (src_root / "settings" / "models.py").write_text(
        "class SettingDefinition: pass\n", encoding="utf-8"
    )
    (src_root / "settings" / "registry.py").write_text(
        "def get_registry(): return None\n", encoding="utf-8"
    )
    for rel, body in (settings_files or {}).items():
        (settings_dir / rel).write_text(
            textwrap.dedent(body).lstrip(), encoding="utf-8"
        )
    for rel, body in (api_files or {}).items():
        (api_dir / rel).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    for rel, body in (extra_files or {}).items():
        target = src_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return tmp_path


def setting_registration(  # noqa: PLR0913 -- fixture builder; each kwarg maps to a SettingDefinition field
    namespace_member: str,
    key: str,
    *,
    setting_type: str = "BOOLEAN",
    default: str | None = '"false"',
    read_only_post_init: bool = False,
    setting_key: str | None = None,
    extra_kwargs: str = "",
) -> str:
    """Render a single ``_r.register(SettingDefinition(...))`` block."""
    parts = [
        f"        namespace=SettingNamespace.{namespace_member},",
        f'        key="{key}",',
        f"        type=SettingType.{setting_type},",
    ]
    if default is not None:
        parts.append(f"        default={default},")
    parts.append('        description="...",')
    parts.append('        group="General",')
    if read_only_post_init:
        parts.append("        restart_required=True,")
        parts.append("        read_only_post_init=True,")
    if setting_key is not None:
        parts.append(f'        setting_key="{setting_key}",')
    if extra_kwargs:
        parts.append(f"        {extra_kwargs}")
    body = "\n".join(parts)
    return textwrap.dedent(f"""
        _r.register(
            SettingDefinition(
{body}
            )
        )
    """).lstrip("\n")


def settings_module(*registrations: str) -> str:
    """Wrap a sequence of registration blocks into a module body."""
    head = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()
    """).lstrip()
    return head + "\n" + "\n".join(registrations) + "\n"
