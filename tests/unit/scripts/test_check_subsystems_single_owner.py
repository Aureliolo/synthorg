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
