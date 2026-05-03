"""Pattern A direct ConfigResolver consumer + class-index tests.

Pattern A scans ghost class files for direct
``ConfigResolver.get_*("<ns>", "<key>")`` calls and matches against
the registry, catching cross-namespace consumption that the
gating-namespace and class-file-containment matchers would miss.
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


# ── Class index / factory resolution ──────────────────────────


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
    index = _MODULE._build_class_index(  # type: ignore[attr-defined]
        repo / "src" / "synthorg"
    )
    assert len(index["Widget"]) == 2
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


def test_pattern_a_skips_non_resolver_receivers(tmp_path: Path) -> None:
    """``.get_*()`` on an unrelated receiver isn't treated as a config read.

    Without receiver validation the lint would flag any
    ``client.get_bool("api", "x")`` or ``cache.get("ns", "key")`` in
    a ghost class file as a ConfigResolver consumption. That's a
    push-blocking false positive on arbitrary helper APIs.
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
                        # Unrelated helper -- not a ConfigResolver.
                        await self.cache.get_bool(
                            "engine", "timeout_enforcement_enabled"
                        )
                        await some_client.get(
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
    assert "engine.timeout_enforcement_enabled" not in flagged, (
        f"non-resolver receivers should not trigger Pattern A; got {flagged}"
    )


def test_factory_alias_import_is_resolved(tmp_path: Path) -> None:
    """``from X import Y as Z`` resolves to the underlying factory function.

    Without alias-aware import resolution, a factory imported as
    ``from synthorg.backup.factory import build_backup_service as
    build_service`` would silently skip ghost detection because the
    AST search would look for ``def build_service(...)`` instead of
    ``def build_backup_service(...)``.
    """
    repo = _make_fake_repo(
        tmp_path,
        api_files={
            "app.py": textwrap.dedent("""
                from synthorg.backup.factory import (
                    build_backup_service as build_service,
                )

                def build_app(config):
                    backup_service = build_service(config)
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
        repo / "src" / "synthorg",
    )
    by_class = {g.class_name: g for g in ghosts}
    assert "BackupService" in by_class, (
        f"aliased factory import should still resolve; got ghosts={ghosts}"
    )
    assert by_class["BackupService"].gating_namespace == "backup"


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
