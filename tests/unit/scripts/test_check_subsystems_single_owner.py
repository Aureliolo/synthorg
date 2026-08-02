"""Tests for the single-wiring-path gate.

The gate exists so a subsystem the reconciler owns cannot also be wired by a
hand-kept second list, which is how ``wire_memory_backend`` came to be missing
from the post-setup rewire while its siblings were in it. These cover the
detection, the defining-module carve-out, the per-line opt-out, and the real
repository.
"""

from pathlib import Path

import pytest
from scripts.check_subsystems_single_owner import main, owned_wiring, scan_repo

pytestmark = pytest.mark.unit

_REGISTRY_REL = "src/synthorg/api/subsystems/registry.py"
_WIRING_REL = "src/synthorg/api/lifecycle_helpers/thing_wiring.py"

_REGISTRY = '''\
"""Fixture registry."""

async def _activate_thing(app_state: object) -> None:
    """Wire the thing."""
    from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing

    await wire_thing(app_state)
'''

_WIRING = '''\
"""Fixture wiring module."""

async def wire_thing(app_state: object) -> None:
    """Wire it."""
'''


def _write_repo(tmp_path: Path, *, caller: str = "") -> Path:
    """Lay out a fake repo with a registry, a wiring module, and a caller.

    Returns:
        The fake repo root.
    """
    for rel, body in ((_REGISTRY_REL, _REGISTRY), (_WIRING_REL, _WIRING)):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if caller:
        (tmp_path / "src/synthorg/api/other.py").write_text(caller, encoding="utf-8")
    return tmp_path


class TestOwnedWiring:
    def test_reads_the_registry_imports(self, tmp_path: Path) -> None:
        (record,) = owned_wiring(_write_repo(tmp_path))
        assert record.name == "wire_thing"
        assert record.module == "synthorg.api.lifecycle_helpers.thing_wiring"

    def test_an_unreadable_registry_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot read"):
            owned_wiring(tmp_path)


class TestScan:
    def test_a_clean_repo_has_no_violation(self, tmp_path: Path) -> None:
        assert scan_repo(_write_repo(tmp_path)) == ()

    def test_a_second_caller_is_flagged(self, tmp_path: Path) -> None:
        caller = (
            "from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(app_state)\n"
        )
        (violation,) = scan_repo(_write_repo(tmp_path, caller=caller))
        assert violation.name == "wire_thing"
        assert violation.path == "src/synthorg/api/other.py"

    def test_a_composite_in_the_defining_module_is_flagged(
        self, tmp_path: Path
    ) -> None:
        # A wrapper running several owned wirers in a fixed order is the same
        # second list one file inwards, so the defining module gets no pass.
        root = _write_repo(tmp_path)
        (root / _WIRING_REL).write_text(
            _WIRING + "\n\nasync def compose(s: object) -> None:\n"
            "    await wire_thing(s)\n",
            encoding="utf-8",
        )
        (violation,) = scan_repo(root)
        assert violation.path == _WIRING_REL

    def test_the_per_line_opt_out_is_honoured(self, tmp_path: Path) -> None:
        caller = (
            "from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(app_state)"
            "  # lint-allow: subsystem-single-owner -- ordering\n"
        )
        assert scan_repo(_write_repo(tmp_path, caller=caller)) == ()

    def test_an_aliased_import_is_flagged(self, tmp_path: Path) -> None:
        # Renaming on import binds a second name for the same function; the
        # call through it is the same second caller.
        caller = (
            "from synthorg.api.lifecycle_helpers.thing_wiring import (\n"
            "    wire_thing as _wt,\n"
            ")\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await _wt(app_state)\n"
        )
        (violation,) = scan_repo(_write_repo(tmp_path, caller=caller))
        assert violation.name == "wire_thing"

    def test_a_module_attribute_call_is_flagged(self, tmp_path: Path) -> None:
        caller = (
            "from synthorg.api.lifecycle_helpers import thing_wiring\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await thing_wiring.wire_thing(app_state)\n"
        )
        (violation,) = scan_repo(_write_repo(tmp_path, caller=caller))
        assert violation.name == "wire_thing"

    def test_the_opt_out_is_honoured_anywhere_in_a_multiline_call(
        self, tmp_path: Path
    ) -> None:
        # The marker belongs on the argument it explains, which for a call
        # spanning lines is not the line carrying the callee.
        caller = (
            "from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(\n"
            "        app_state,"
            "  # lint-allow: subsystem-single-owner -- ordering\n"
            "    )\n"
        )
        assert scan_repo(_write_repo(tmp_path, caller=caller)) == ()

    def test_a_namesake_in_another_module_is_left_alone(self, tmp_path: Path) -> None:
        # Two modules can define a ``wire_thing``; only the one the registry
        # activates is owned, and flagging the other reports a subsystem
        # relationship that does not exist.
        root = _write_repo(tmp_path)
        (root / "src/synthorg/tools/thing_wiring.py").parent.mkdir(
            parents=True, exist_ok=True
        )
        (root / "src/synthorg/tools/thing_wiring.py").write_text(
            _WIRING, encoding="utf-8"
        )
        (root / "src/synthorg/api/other.py").write_text(
            "from synthorg.tools.thing_wiring import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(app_state)\n",
            encoding="utf-8",
        )
        assert scan_repo(root) == ()

    def test_a_re_export_of_the_owned_function_is_flagged(self, tmp_path: Path) -> None:
        # Importing through the package rather than the defining module is the
        # same function, so routing around the gate that way must not work.
        root = _write_repo(tmp_path)
        (root / "src/synthorg/api/lifecycle_helpers/__init__.py").write_text(
            "from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing\n"
            "\n"
            '__all__ = ["wire_thing"]\n',
            encoding="utf-8",
        )
        (root / "src/synthorg/api/other.py").write_text(
            "from synthorg.api.lifecycle_helpers import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(app_state)\n",
            encoding="utf-8",
        )
        flagged = {violation.path for violation in scan_repo(root)}
        assert "src/synthorg/api/other.py" in flagged

    def test_a_teardown_is_not_owned_wiring(self, tmp_path: Path) -> None:
        # ``unwire_thing`` contains "wire" but is the teardown half, which the
        # registry calls from its own deactivate adapter.
        root = _write_repo(tmp_path)
        (root / _REGISTRY_REL).write_text(
            _REGISTRY + "\n\nasync def _deactivate_thing(app_state: object) -> None:\n"
            "    from synthorg.api.lifecycle_helpers.thing_wiring import unwire_thing\n"
            "\n"
            "    await unwire_thing(app_state)\n",
            encoding="utf-8",
        )
        (root / "src/synthorg/api/other.py").write_text(
            "from synthorg.api.lifecycle_helpers.thing_wiring import unwire_thing\n"
            "\n"
            "async def teardown(app_state: object) -> None:\n"
            "    await unwire_thing(app_state)\n",
            encoding="utf-8",
        )
        assert scan_repo(root) == ()


class TestGate:
    def test_passes_on_a_clean_repo(self, tmp_path: Path) -> None:
        assert main(["--repo-root", str(_write_repo(tmp_path))]) == 0

    def test_fails_on_a_second_caller(self, tmp_path: Path) -> None:
        caller = (
            "from synthorg.api.lifecycle_helpers.thing_wiring import wire_thing\n"
            "\n"
            "async def boot(app_state: object) -> None:\n"
            "    await wire_thing(app_state)\n"
        )
        assert main(["--repo-root", str(_write_repo(tmp_path, caller=caller))]) == 1


def test_the_real_repo_passes() -> None:
    """Every declared subsystem has exactly one wiring path today."""
    assert main([]) == 0
