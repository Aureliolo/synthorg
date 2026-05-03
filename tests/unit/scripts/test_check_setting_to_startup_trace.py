"""Unit tests for scripts/check_setting_to_startup_trace.py.

The lint detects "ghost-wired" settings -- registered settings whose
consuming machinery exists but is never instantiated at boot. Two
detection paths cover the known positives:

1. Hardcoded-None ghost: ``x: T | None = None`` at module scope in
   ``api/{app, lifecycle, ...}.py`` paired with a conditional
   ``if x is not None: x.start()`` gate. ApprovalTimeoutScheduler.
2. Factory-gated ghost: ``x = factory(...)`` where ``factory`` returns
   ``None`` when ``not config.<ns>.<flag>`` is the registered default.
   BackupService.

For each ghost service T, settings are matched to T via:

- gating namespace (factory-gated case): every setting in the gating
  namespace is ghost-wired.
- class-file containment (hardcoded-None case): a setting is
  ghost-wired iff its key appears as a string literal in T's class
  file AND its namespace appears in T's class file path.

Tests load the script as a module and call its public helpers
directly rather than spawning subprocesses -- the script discovers
its project root from ``--repo-root`` / ``__file__``.
"""

import importlib.util
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

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


_MODULE = _load_script_module()


# ── Fake-repo helpers ───────────────────────────────────────────


def _make_fake_repo(
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
    # The lint expects ``settings/enums.py`` to define SettingNamespace
    # so AST resolution of ``SettingNamespace.X.value`` works. The
    # fake repo replicates only the enum members the test uses.
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


def _setting_registration(  # noqa: PLR0913 -- fixture builder; each kwarg maps to a SettingDefinition field
    namespace_member: str,
    key: str,
    *,
    setting_type: str = "BOOLEAN",
    default: str | None = '"false"',
    read_only_post_init: bool = False,
    yaml_path: str | None = None,
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
    if yaml_path is not None:
        parts.append(f'        yaml_path="{yaml_path}",')
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


def _settings_module(*registrations: str) -> str:
    """Wrap a sequence of registration blocks into a module body."""
    head = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()
    """).lstrip()
    return head + "\n" + "\n".join(registrations) + "\n"


# ── Settings-inventory loader ───────────────────────────────────


def test_inventory_extracts_namespace_key_and_metadata(tmp_path: Path) -> None:
    """Loader returns one record per registered SettingDefinition."""
    repo = _make_fake_repo(
        tmp_path,
        settings_files={
            "backup.py": _settings_module(
                _setting_registration("BACKUP", "enabled"),
                _setting_registration("BACKUP", "path", setting_type="STRING"),
            ),
        },
    )
    records = _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
        repo / "src" / "synthorg" / "settings" / "definitions"
    )
    yaml_paths = {r.yaml_path for r in records}
    assert yaml_paths == {"backup.enabled", "backup.path"}


def test_inventory_skips_read_only_post_init(tmp_path: Path) -> None:
    """``read_only_post_init=True`` settings are tagged so the lint can skip."""
    repo = _make_fake_repo(
        tmp_path,
        settings_files={
            "security.py": _settings_module(
                _setting_registration(
                    "SECURITY",
                    "auth_token_bytes",
                    setting_type="INTEGER",
                    default='"32"',
                    read_only_post_init=True,
                ),
                _setting_registration("SECURITY", "audit_enabled"),
            ),
        },
    )
    records = _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
        repo / "src" / "synthorg" / "settings" / "definitions"
    )
    by_key = {r.key: r for r in records}
    assert by_key["auth_token_bytes"].read_only_post_init is True
    assert by_key["audit_enabled"].read_only_post_init is False


def test_inventory_uses_explicit_yaml_path(tmp_path: Path) -> None:
    """Explicit ``yaml_path=`` overrides the ``namespace.key`` default."""
    repo = _make_fake_repo(
        tmp_path,
        settings_files={
            "observability.py": _settings_module(
                _setting_registration(
                    "ENGINE",
                    "timeout_enforcement_enabled",
                    yaml_path="engine.timeout_enforcement_enabled",
                ),
            ),
        },
    )
    records = _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
        repo / "src" / "synthorg" / "settings" / "definitions"
    )
    assert records[0].yaml_path == "engine.timeout_enforcement_enabled"


def test_inventory_records_suppression_marker(tmp_path: Path) -> None:
    """Trailing ``# lint-allow: bootstrap-wiring -- <reason>`` is captured."""
    body = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()

        _r.register(
            SettingDefinition(
                namespace=SettingNamespace.BACKUP,
                key="enabled",
                type=SettingType.BOOLEAN,
                default="false",
                description="...",
                group="General",
            )
        )  # lint-allow: bootstrap-wiring -- legacy operator-gated bootstrap
    """).lstrip()
    repo = _make_fake_repo(tmp_path, settings_files={"backup.py": body})
    records = _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
        repo / "src" / "synthorg" / "settings" / "definitions"
    )
    assert records[0].has_suppression is True


def test_inventory_rejects_empty_suppression_justification(tmp_path: Path) -> None:
    """Empty justification after ``--`` does not count as suppression."""
    body = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()

        _r.register(
            SettingDefinition(
                namespace=SettingNamespace.BACKUP,
                key="enabled",
                type=SettingType.BOOLEAN,
                default="false",
                description="...",
                group="General",
            )
        )  # lint-allow: bootstrap-wiring --
    """).lstrip()
    repo = _make_fake_repo(tmp_path, settings_files={"backup.py": body})
    records = _MODULE.load_setting_definitions(  # type: ignore[attr-defined]
        repo / "src" / "synthorg" / "settings" / "definitions"
    )
    assert records[0].has_suppression is False


# ── Hardcoded-None ghost-service detection ─────────────────────


def test_detects_hardcoded_none_ghost_with_conditional_start(tmp_path: Path) -> None:
    """``x: T | None = None`` + later ``if x is not None: x.start()`` flags T."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.security.timeout.scheduler import (
                    ApprovalTimeoutScheduler,
                )

                def build_app():
                    approval_timeout_scheduler: ApprovalTimeoutScheduler | None = None
                    return approval_timeout_scheduler
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(approval_timeout_scheduler):
                    if approval_timeout_scheduler is not None:
                        approval_timeout_scheduler.start()
            """).lstrip(),
        },
    )
    ghosts = _MODULE.find_hardcoded_none_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    classes = {g.class_name for g in ghosts}
    assert "ApprovalTimeoutScheduler" in classes


def test_skips_hardcoded_none_without_conditional_start(tmp_path: Path) -> None:
    """A hardcoded-None variable that is never used in a start gate is not a ghost.

    Conservative -- avoids flagging dead-but-not-wired-to-start variables.
    """
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from typing import Optional

                class SomeService:
                    pass

                def build():
                    svc: SomeService | None = None
                    return svc
            """).lstrip(),
            "lifecycle.py": "async def startup(): pass\n",
        },
    )
    ghosts = _MODULE.find_hardcoded_none_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    assert all(g.class_name != "SomeService" for g in ghosts)


# ── Factory-gated ghost-service detection ──────────────────────


def test_detects_factory_gated_ghost_via_default_disabled_flag(tmp_path: Path) -> None:
    """Factory returning ``None`` when default-disabled flag is False is a ghost."""
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
    ghosts = _MODULE.find_factory_gated_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    by_class = {g.class_name: g for g in ghosts}
    assert "BackupService" in by_class
    assert by_class["BackupService"].gating_namespace == "backup"


def test_factory_gated_ghost_only_when_default_is_disabled(tmp_path: Path) -> None:
    """If the gating setting's registered default is enabled, NOT a ghost."""
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
                _setting_registration("BACKUP", "enabled", default='"true"'),
            ),
        },
    )
    ghosts = _MODULE.find_factory_gated_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    assert all(g.class_name != "BackupService" for g in ghosts)


# ── Setting → ghost matching ───────────────────────────────────


def test_setting_matched_via_factory_gating_namespace(tmp_path: Path) -> None:
    """Every setting in the factory-gating namespace flags as ghost-wired."""
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
                _setting_registration(
                    "BACKUP",
                    "schedule_hours",
                    setting_type="INTEGER",
                    default='"6"',
                ),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    flagged_keys = {v.yaml_path for v in violations}
    assert flagged_keys == {
        "backup.enabled",
        "backup.path",
        "backup.schedule_hours",
    }


def test_setting_matched_via_class_file_containment(tmp_path: Path) -> None:
    """Hardcoded-None ghost matches by key-in-class-file + namespace-in-path."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.security.timeout.scheduler import (
                    ApprovalTimeoutScheduler,
                )

                def build():
                    sched: ApprovalTimeoutScheduler | None = None
                    return sched
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(sched):
                    if sched is not None:
                        sched.start()
            """).lstrip(),
        },
        extra_files={
            "security/__init__.py": "",
            "security/timeout/__init__.py": "",
            "security/timeout/scheduler.py": textwrap.dedent("""
                class ApprovalTimeoutScheduler:
                    \"\"\"Scheduler that polls approvals.

                    Reads ``security.timeout_check_interval_seconds`` via
                    ConfigResolver at the call site.
                    \"\"\"
                    def __init__(self, *, interval_seconds: float) -> None:
                        self._interval = interval_seconds
            """).lstrip(),
        },
        settings_files={
            "security.py": _settings_module(
                _setting_registration(
                    "SECURITY",
                    "timeout_check_interval_seconds",
                    setting_type="FLOAT",
                    default='"60.0"',
                ),
                _setting_registration("SECURITY", "audit_enabled"),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    flagged_keys = {v.yaml_path for v in violations}
    assert "security.timeout_check_interval_seconds" in flagged_keys
    assert "security.audit_enabled" not in flagged_keys


def test_read_only_post_init_setting_skipped(tmp_path: Path) -> None:
    """``read_only_post_init=True`` settings are skipped even when in ghost ns."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.security.timeout.scheduler import (
                    ApprovalTimeoutScheduler,
                )

                def build():
                    sched: ApprovalTimeoutScheduler | None = None
                    return sched
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(sched):
                    if sched is not None:
                        sched.start()
            """).lstrip(),
        },
        extra_files={
            "security/__init__.py": "",
            "security/timeout/__init__.py": "",
            "security/timeout/scheduler.py": textwrap.dedent("""
                class ApprovalTimeoutScheduler:
                    \"\"\"Reads timeout_check_interval_seconds + auth_token_bytes.\"\"\"
                    pass
            """).lstrip(),
        },
        settings_files={
            "security.py": _settings_module(
                _setting_registration(
                    "SECURITY",
                    "auth_token_bytes",
                    setting_type="INTEGER",
                    default='"32"',
                    read_only_post_init=True,
                ),
                _setting_registration(
                    "SECURITY",
                    "timeout_check_interval_seconds",
                    setting_type="FLOAT",
                    default='"60.0"',
                ),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    flagged_keys = {v.yaml_path for v in violations}
    assert "security.auth_token_bytes" not in flagged_keys
    assert "security.timeout_check_interval_seconds" in flagged_keys


def test_suppression_marker_silences_violation(tmp_path: Path) -> None:
    """Valid suppression marker on registration line silences the flag."""
    body = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()

        _r.register(
            SettingDefinition(
                namespace=SettingNamespace.BACKUP,
                key="enabled",
                type=SettingType.BOOLEAN,
                default="false",
                description="...",
                group="General",
            )
        )  # lint-allow: bootstrap-wiring -- operator-gated bootstrap is by design
    """).lstrip()
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import build_backup_service

                def build(config):
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
        settings_files={"backup.py": body},
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    assert violations == []


# ── Negative cases (must NOT flag) ─────────────────────────────


def test_unrelated_setting_in_non_ghost_namespace_passes(tmp_path: Path) -> None:
    """A setting in a namespace with no ghost service is silent."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                async def startup():
                    pass
            """).lstrip(),
            "lifecycle.py": "async def startup(): pass\n",
        },
        settings_files={
            "engine.py": _settings_module(
                _setting_registration("ENGINE", "timeout_enforcement_enabled"),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    assert violations == []


def test_setting_consumed_by_started_class_passes(tmp_path: Path) -> None:
    """A setting whose namespace is not a ghost-namespace passes silently."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                async def startup():
                    # api.* consumers live in lifecycle_helpers and
                    # run unconditionally; no None-gate, no factory.
                    pass
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup():
                    pass
            """).lstrip(),
            "lifecycle_helpers.py": textwrap.dedent("""
                async def apply_bridge_config(state):
                    state.set_ws_frame_timeout(30)
            """).lstrip(),
        },
        settings_files={
            "api.py": _settings_module(
                _setting_registration(
                    "API",
                    "ws_frame_timeout_seconds",
                    setting_type="INTEGER",
                    default='"30"',
                ),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    assert violations == []


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
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n")
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
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n")
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
    baseline.write_text("backup.enabled:ghost-wired:BackupService\n")
    new, stale = _MODULE.run_with_baseline(  # type: ignore[attr-defined]
        repo,
        baseline_path=baseline,
    )
    assert new == []
    assert stale == ["backup.enabled:ghost-wired:BackupService"]


# ── Real-repo smoke test (load-bearing) ─────────────────────────


def test_real_repo_violations_match_expected() -> None:
    """Lint against the actual src/synthorg/ tree.

    Asserts exactly the 8 expected ghost-wired violations:

      - 7 ``backup.*`` settings (BackupService factory-gated by default).
      - ``security.timeout_check_interval_seconds`` (ApprovalTimeoutScheduler
        hardcoded to None in app.py).

    Asserts zero false positives on the negative ``security.*`` settings
    (audit_enabled, post_tool_scanning_enabled, ...) and on
    ``engine.timeout_enforcement_enabled`` and the WS DoS settings.

    This test is the load-bearing assertion that the lint logic is
    correct against real-world wiring. If it fails, the lint logic is
    wrong; baseline drift is irrelevant here -- no baseline is loaded.
    """
    violations = _MODULE.scan_repo(  # type: ignore[attr-defined]
        _REPO_ROOT,
        baseline_path=None,
    )
    flagged = {v.yaml_path for v in violations}

    expected_positives = {
        "backup.compression",
        "backup.enabled",
        "backup.on_shutdown",
        "backup.on_startup",
        "backup.path",
        "backup.retention_days",
        "backup.schedule_hours",
        "security.timeout_check_interval_seconds",
    }
    assert expected_positives.issubset(flagged), (
        f"missing expected positives: {expected_positives - flagged}"
    )

    must_not_flag = {
        "security.enabled",
        "security.audit_enabled",
        "security.post_tool_scanning_enabled",
        "security.output_scan_policy_type",
        "security.audit_retention_days",
        "security.retention_cleanup_paused",
        "security.auth_token_bytes",
        "engine.timeout_enforcement_enabled",
    }
    leaked = flagged & must_not_flag
    assert not leaked, f"false positives on negatives: {leaked}"

    # Strict equality: nothing else should be flagged. If the lint
    # detects a NEW ghost-wired setting, the baseline + this test
    # both update intentionally.
    extras = flagged - expected_positives
    assert not extras, f"unexpected extra flags: {extras}"


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
        _MODULE._load_lifecycle_trees(repo / "src" / "synthorg")  # type: ignore[attr-defined]


def test_load_baseline_missing_file_returns_empty_set(tmp_path: Path) -> None:
    """Missing baseline file is treated as empty allowlist (new lint)."""
    nonexistent = tmp_path / "does-not-exist.txt"
    result = _MODULE._load_baseline(nonexistent)  # type: ignore[attr-defined]
    assert result == set()


def test_load_baseline_malformed_entry_raises_valueerror(tmp_path: Path) -> None:
    """Wrong-field-count baseline entry fails loud."""
    baseline = tmp_path / "baseline.txt"
    # Two fields instead of three.
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


# ── CLI argument tests ────────────────────────────────────────


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


# ── Class ambiguity / factory resolution failures ─────────────


def test_class_index_records_multiple_definitions(tmp_path: Path) -> None:
    """Same class name in two files makes the lint refuse to resolve."""
    repo = _make_fake_repo(
        tmp_path,
        extra_files={
            "alpha/__init__.py": "",
            "alpha/widget.py": "class Widget: pass\n",
            "beta/__init__.py": "",
            "beta/widget.py": "class Widget: pass\n",
        },
    )
    index = _MODULE._build_class_index(repo / "src" / "synthorg")  # type: ignore[attr-defined]
    assert len(index["Widget"]) == 2
    # Resolution refuses when ambiguous.
    assert (
        _MODULE._resolve_class_file("Widget", index)  # type: ignore[attr-defined]
        is None
    )


def test_factory_function_not_found_skips_silently(tmp_path: Path) -> None:
    """A factory imported but not defined in the source module is skipped."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.missing.factory import build_thing

                def build_app(config):
                    thing = build_thing(config)
                    return thing
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(thing):
                    if thing is not None:
                        await thing.start()
            """).lstrip(),
        },
    )
    # Factory module doesn't exist; ghost detection skips silently.
    ghosts = _MODULE.find_factory_gated_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg",
        settings_by_yaml={},
    )
    assert ghosts == []


# ── Pattern A: ConfigResolver consumer discovery ──────────────


def test_pattern_a_flags_cross_namespace_consumption(tmp_path: Path) -> None:
    """Hardcoded-None ghost reading a setting in a different namespace.

    The class-file-containment matcher would NOT fire here because
    the ghost lives in ``api/`` but reads ``engine.X`` -- the ``engine``
    segment is not in the class file's path. Pattern A catches it via
    direct ``ConfigResolver.get_*`` call inspection.
    """
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.api.foo import FooService

                def build():
                    foo: FooService | None = None
                    return foo
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(foo):
                    if foo is not None:
                        foo.start()
            """).lstrip(),
            "foo.py": textwrap.dedent("""
                class FooService:
                    async def start(self):
                        await self.config_resolver.get_bool(
                            "engine", "timeout_enforcement_enabled"
                        )
            """).lstrip(),
        },
        settings_files={
            "engine.py": _settings_module(
                _setting_registration("ENGINE", "timeout_enforcement_enabled"),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    flagged = {v.yaml_path for v in violations}
    assert "engine.timeout_enforcement_enabled" in flagged


def test_pattern_a_resolves_setting_namespace_enum(tmp_path: Path) -> None:
    """Pattern A resolves ``SettingNamespace.X.value`` to the namespace string."""
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.api.foo import FooService

                def build():
                    foo: FooService | None = None
                    return foo
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                async def startup(foo):
                    if foo is not None:
                        foo.start()
            """).lstrip(),
            "foo.py": textwrap.dedent("""
                from synthorg.settings.enums import SettingNamespace

                class FooService:
                    async def start(self):
                        await self.config_resolver.get_int(
                            SettingNamespace.ENGINE.value, "timeout_enforcement_enabled"
                        )
            """).lstrip(),
        },
        settings_files={
            "engine.py": _settings_module(
                _setting_registration("ENGINE", "timeout_enforcement_enabled"),
            ),
        },
    )
    violations = _MODULE.scan_repo(repo, baseline_path=None)  # type: ignore[attr-defined]
    flagged = {v.yaml_path for v in violations}
    assert "engine.timeout_enforcement_enabled" in flagged


# ── Type invariant tests ──────────────────────────────────────


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
