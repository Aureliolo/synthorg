"""Baseline file + CLI + error-path + type-invariant tests.

Covers:

- Baseline subtraction (subsumed / new violation / stale entry).
- Baseline file parse errors (malformed / empty fields / extra fields / missing).
- ``main()`` exit-code semantics (--repo-root invalid, --update-baseline,
  --baseline custom path).
- Loader / lifecycle parse-error escalation (silent-skip is a regression
  the lint must reject).
- ``GhostService`` and ``Violation`` ``__post_init__`` invariant
  validators.
"""

import textwrap
from pathlib import Path

import pytest

from tests.unit.scripts._setting_to_startup_trace_helpers import (
    MODULE as _MODULE,
)
from tests.unit.scripts._setting_to_startup_trace_helpers import (
    make_fake_repo as _make_fake_repo,
)
from tests.unit.scripts._setting_to_startup_trace_helpers import (
    setting_registration as _setting_registration,
)
from tests.unit.scripts._setting_to_startup_trace_helpers import (
    settings_module as _settings_module,
)

pytestmark = pytest.mark.unit


# ── Baseline behaviour ──────────────────────────────────────────


def test_baseline_subsumed_passes(tmp_path: Path) -> None:
    """When all current violations are listed in the baseline, exit clean."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import build_backup_service

                def build_app(config):
                    backup_service = build_backup_service(config)
                    return backup_service
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(backup_service):
                    if backup_service is not None:
                        await backup_service.start()
            """).lstrip(),
        },
        extra_files={
            "backup/__init__.py": "",
            "backup/factory.py": textwrap.dedent("""
                from synthorg.backup.service import BackupService

                def build_backup_service(config) -> BackupService | None:
                    if not config.backup.enabled:
                        return None
                    return BackupService(config.backup)
            """).lstrip(),
            "backup/service.py": "class BackupService: pass\n",
        },
        settings_files={
            "backup.py": _settings_module(
                _setting_registration("BACKUP", "enabled"),
            ),
        },
    )
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n", encoding="utf-8")
    new, stale = _MODULE.run_with_baseline(  # type: ignore[attr-defined]
        repo,
        baseline_path=baseline,
    )
    assert new == []
    assert stale == []


def test_baseline_new_violation_fails(tmp_path: Path) -> None:
    """A violation absent from the baseline is reported as new."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import build_backup_service

                def build_app(config):
                    backup_service = build_backup_service(config)
                    return backup_service
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(backup_service):
                    if backup_service is not None:
                        await backup_service.start()
            """).lstrip(),
        },
        extra_files={
            "backup/__init__.py": "",
            "backup/factory.py": textwrap.dedent("""
                from synthorg.backup.service import BackupService

                def build_backup_service(config) -> BackupService | None:
                    if not config.backup.enabled:
                        return None
                    return BackupService(config.backup)
            """).lstrip(),
            "backup/service.py": "class BackupService: pass\n",
        },
        settings_files={
            "backup.py": _settings_module(
                _setting_registration("BACKUP", "enabled"),
                _setting_registration("BACKUP", "path", setting_type="STRING"),
            ),
        },
    )
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n", encoding="utf-8")
    new, stale = _MODULE.run_with_baseline(  # type: ignore[attr-defined]
        repo,
        baseline_path=baseline,
    )
    assert {v.yaml_path for v in new} == {"backup.path"}
    assert stale == []


def test_baseline_stale_entry_warns_but_passes(tmp_path: Path) -> None:
    """An entry in baseline but not in current violations warns but doesn't fail."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": "async def startup(): pass\n",
            "lifecycle.py": "async def startup(): pass\n",
        },
        settings_files={
            "engine.py": _settings_module(
                _setting_registration("ENGINE", "timeout_enforcement_enabled"),
            ),
        },
    )
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n", encoding="utf-8")
    new, stale = _MODULE.run_with_baseline(  # type: ignore[attr-defined]
        repo,
        baseline_path=baseline,
    )
    assert new == []
    assert stale == ["backup.enabled:ghost-wired:BackupService"]


# ── Error-path tests ───────────────────────────────────────────


def test_load_setting_definitions_raises_on_syntax_error(tmp_path: Path) -> None:
    """A syntax error in any definitions file fails loud with file:line."""
    repo = _make_fake_repo(
        tmp_path,
        settings_files={"broken.py": "this is = not python\n"},
    )
    with pytest.raises(ValueError, match="syntax error"):
        _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
            repo / "src" / "synthorg" / "settings" / "definitions"
        )


def test_load_lifecycle_trees_raises_on_syntax_error(tmp_path: Path) -> None:
    """A syntax error in any lifecycle file fails loud with file:line."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": "this is not python (\n",
            "lifecycle.py": "async def startup(): pass\n",
        },
    )
    with pytest.raises(ValueError, match="syntax error"):
        _MODULE._load_lifecycle_trees(  # type: ignore[attr-defined]
            repo / "src" / "synthorg"
        )


def test_load_baseline_missing_file_returns_empty_set(tmp_path: Path) -> None:
    """Missing baseline file is treated as empty allowlist (new lint)."""
    nonexistent = tmp_path / "does-not-exist.txt"
    result = _MODULE._load_baseline(nonexistent)  # type: ignore[attr-defined]
    assert result == set()


def test_load_baseline_malformed_entry_raises_valueerror(tmp_path: Path) -> None:
    """Wrong-field-count baseline entry fails loud."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("backup.enabled:BackupService\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed baseline entry"):
        _MODULE._load_baseline(baseline)  # type: ignore[attr-defined]


def test_load_baseline_empty_field_raises_valueerror(tmp_path: Path) -> None:
    """Empty middle/end field is treated as malformed."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("backup.enabled::BackupService\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed baseline entry"):
        _MODULE._load_baseline(baseline)  # type: ignore[attr-defined]


def test_load_baseline_extra_fields_raises_valueerror(tmp_path: Path) -> None:
    """Four-field baseline entry is rejected as malformed."""
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "backup.enabled:ghost-wired:BackupService:extra\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="malformed baseline entry"):
        _MODULE._load_baseline(baseline)  # type: ignore[attr-defined]


# ── CLI tests ──────────────────────────────────────────────────


def test_main_repo_root_not_accessible_exits_2(tmp_path: Path) -> None:
    """Main exits 2 when --repo-root points to a nonexistent directory."""
    nonexistent = tmp_path / "nonexistent"
    result = _MODULE.main(["--repo-root", str(nonexistent)])  # type: ignore[attr-defined]
    assert result == 2


def test_main_update_baseline_writes_file(tmp_path: Path) -> None:
    """``--update-baseline`` rewrites the baseline with current violations."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import build_backup_service

                def build_app(config):
                    backup_service = build_backup_service(config)
                    return backup_service
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(backup_service):
                    if backup_service is not None:
                        await backup_service.start()
            """).lstrip(),
        },
        extra_files={
            "backup/__init__.py": "",
            "backup/factory.py": textwrap.dedent("""
                from synthorg.backup.service import BackupService

                def build_backup_service(config) -> BackupService | None:
                    if not config.backup.enabled:
                        return None
                    return BackupService(config.backup)
            """).lstrip(),
            "backup/service.py": "class BackupService: pass\n",
        },
        settings_files={
            "backup.py": _settings_module(
                _setting_registration("BACKUP", "enabled"),
            ),
        },
    )
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("stale content\n", encoding="utf-8")
    rc = _MODULE.main(  # type: ignore[attr-defined]
        ["--repo-root", str(repo), "--baseline", str(baseline), "--update-baseline"]
    )
    assert rc == 0
    body = baseline.read_text(encoding="utf-8")
    assert "backup.enabled:ghost-wired:BackupService" in body
    assert "stale content" not in body


def test_main_custom_baseline_path_respected(tmp_path: Path) -> None:
    """``--baseline`` flag uses the custom path for subtraction."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import build_backup_service

                def build_app(config):
                    backup_service = build_backup_service(config)
                    return backup_service
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(backup_service):
                    if backup_service is not None:
                        await backup_service.start()
            """).lstrip(),
        },
        extra_files={
            "backup/__init__.py": "",
            "backup/factory.py": textwrap.dedent("""
                from synthorg.backup.service import BackupService

                def build_backup_service(config) -> BackupService | None:
                    if not config.backup.enabled:
                        return None
                    return BackupService(config.backup)
            """).lstrip(),
            "backup/service.py": "class BackupService: pass\n",
        },
        settings_files={
            "backup.py": _settings_module(
                _setting_registration("BACKUP", "enabled"),
            ),
        },
    )
    custom = tmp_path / "custom.txt"
    custom.write_text("backup.enabled:ghost-wired:BackupService\n", encoding="utf-8")
    rc = _MODULE.main(  # type: ignore[attr-defined]
        ["--repo-root", str(repo), "--baseline", str(custom)]
    )
    assert rc == 0  # violation subsumed by custom baseline


# ── Type invariants ────────────────────────────────────────────


def test_ghost_service_rejects_kind_gating_namespace_mismatch() -> None:
    """``__post_init__`` rejects (kind, gating_namespace) mismatches."""
    with pytest.raises(ValueError, match="gating_namespace"):
        _MODULE.GhostService(  # type: ignore[attr-defined]
            class_name="Foo",
            kind="hardcoded-none",
            gating_namespace="backup",  # wrong: should be None for hardcoded-none
            source_file="src/synthorg/api/app.py",
        )
    with pytest.raises(ValueError, match="gating_namespace"):
        _MODULE.GhostService(  # type: ignore[attr-defined]
            class_name="Bar",
            kind="factory-gated",
            gating_namespace=None,  # wrong: factory-gated needs a namespace
            source_file="src/synthorg/api/app.py",
        )


def test_violation_rejects_colon_in_yaml_path() -> None:
    """Colon in ``yaml_path`` would corrupt baseline format; reject at construction."""
    with pytest.raises(ValueError, match="yaml_path"):
        _MODULE.Violation(  # type: ignore[attr-defined]
            yaml_path="bad:path",
            kind="ghost-wired",
            owning_class="X",
            source_file="src/synthorg/settings/definitions/x.py",
            source_line=1,
            reason="...",
        )


def test_violation_rejects_colon_in_owning_class() -> None:
    """Colon in ``owning_class`` corrupts baseline format; reject at construction."""
    with pytest.raises(ValueError, match="owning_class"):
        _MODULE.Violation(  # type: ignore[attr-defined]
            yaml_path="x.y",
            kind="ghost-wired",
            owning_class="bad:class",
            source_file="src/synthorg/settings/definitions/x.py",
            source_line=1,
            reason="...",
        )
