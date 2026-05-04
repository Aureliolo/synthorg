"""Inventory-loader + real-repo smoke tests for the bootstrap-wiring lint.

The lint detects "ghost-wired" settings -- registered settings whose
consuming machinery exists but is never instantiated at boot. Two
detection paths cover the known positives:

1. Hardcoded-None ghost: ``x: T | None = None`` at module scope in
   ``api/{app, lifecycle, ...}.py`` paired with a conditional
   ``if x is not None: x.start()`` gate. ApprovalTimeoutScheduler.
2. Factory-gated ghost: ``x = factory(...)`` where ``factory`` returns
   ``None`` when ``not config.<ns>.<flag>`` is the registered default.
   BackupService.

Per-category test modules in this directory cover the rest of the
behaviour matrix; this module owns the inventory loader and the
load-bearing real-repo smoke test.
"""

import textwrap
from pathlib import Path

import pytest

from tests.unit.scripts._setting_to_startup_trace_helpers import (
    MODULE as _MODULE,
)
from tests.unit.scripts._setting_to_startup_trace_helpers import (
    REPO_ROOT as _REPO_ROOT,
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


# ── Real-repo smoke test (load-bearing) ─────────────────────────


def test_real_repo_violations_match_expected() -> None:
    """Lint against the actual src/synthorg/ tree.

    Asserts exactly the 7 expected ghost-wired violations: the
    ``backup.*`` settings the BackupService factory still resolves
    only when the operator opts in.  ``security.timeout_check_interval_seconds``
    used to ghost-wire here as well; the audit-bucket PR wired
    ``ApprovalTimeoutScheduler`` into the lifespan startup so the
    setting is now resolved on every cold start, and the lint should
    no longer flag it.

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
        "security.timeout_check_interval_seconds",
        "engine.timeout_enforcement_enabled",
    }
    leaked = flagged & must_not_flag
    assert not leaked, f"false positives on negatives: {leaked}"

    extras = flagged - expected_positives
    assert not extras, f"unexpected extra flags: {extras}"
