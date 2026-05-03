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
