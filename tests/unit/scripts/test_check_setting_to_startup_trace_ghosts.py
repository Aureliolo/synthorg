"""Ghost-service detection + setting-to-ghost matching tests.

Covers:

- Hardcoded-None ghost detection (with + without conditional start).
- Factory-gated ghost detection (with + without registered default-disabled flag).
- Setting matching via gating namespace + class-file containment.
- Suppression markers and ``read_only_post_init=True`` skipping.
- Negative cases (settings in non-ghost namespaces).
- Scope-aware binding (same name, different class).
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


# ── Hardcoded-None ghost detection ─────────────────────────────


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


# ── Factory-gated ghost detection ──────────────────────────────


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


# ── Scope-aware ghost detection ─────────────────────────────────


def test_scope_aware_skips_same_name_different_class(tmp_path: Path) -> None:
    """Same variable name with different annotated class doesn't piggy-back.

    The hardcoded-None candidate ``foo: FooService | None = None`` in
    ``app.py`` must not be flagged via a start gate in ``lifecycle.py``
    where the parameter ``foo: BarService | None`` belongs to a
    different type. Without scope-aware matching, the lint would
    treat any ``if foo is not None: foo.start()`` as proof that
    FooService starts, regardless of which type is bound.
    """
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.foo.service import FooService

                def build():
                    foo: FooService | None = None
                    return foo
            """).lstrip(),
            "lifecycle.py": textwrap.dedent("""
                from synthorg.bar.service import BarService

                async def startup(foo: BarService | None):
                    if foo is not None:
                        foo.start()
            """).lstrip(),
        },
        extra_files={
            "foo/__init__.py": "",
            "foo/service.py": textwrap.dedent("""
                class FooService:
                    \"\"\"reads timeout_check_interval_seconds.\"\"\"
            """).lstrip(),
            "bar/__init__.py": "",
            "bar/service.py": "class BarService: pass\n",
        },
        settings_files={
            "security.py": _settings_module(
                _setting_registration(
                    "SECURITY",
                    "timeout_check_interval_seconds",
                    setting_type="FLOAT",
                    default='"60.0"',
                ),
            ),
        },
    )
    ghosts = _MODULE.find_hardcoded_none_ghosts(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    foo_ghosts = [g for g in ghosts if g.class_name == "FooService"]
    assert foo_ghosts == [], (
        f"FooService should not be flagged when only BarService binding "
        f"hits the start gate; got {foo_ghosts}"
    )
