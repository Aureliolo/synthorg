"""Tests for the subsystem-decline-reason gate.

``GET /subsystems`` exists to answer "why is this not up". An activation that
returns without installing its capability and without naming its condition
leaves the endpoint nothing to say but "declined; see the wiring log", which
is the endpoint telling the operator to read a container log instead.

These cover each of the three ways a subsystem satisfies the rule, the shape
that must be rejected, the idempotency guard that must NOT be read as a
decline, the one-call-inward chain, and the real registry.
"""

import ast
from pathlib import Path

import pytest
from scripts.check_subsystem_decline_reason import (
    _has_early_return,
    _raises_declined,
    main,
    read_specs,
    scan_repo,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _fn(source: str) -> ast.AST:
    """Parse one function body for the predicate helpers.

    Returns:
        The parsed function node.
    """
    return ast.parse(source).body[0]


class TestDeclineDetection:
    def test_an_absence_guard_is_a_decline(self) -> None:
        # The shape the gate exists for: the activation backs out because a
        # collaborator it needs is not there, and says nothing about which.
        assert _has_early_return(
            _fn(
                "def activate(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "    state.install()\n"
            )
        )

    def test_a_falsy_guard_is_a_decline(self) -> None:
        assert _has_early_return(
            _fn(
                "def activate(state):\n"
                "    if not state.enabled:\n"
                "        return\n"
                "    state.install()\n"
            )
        )

    def test_an_idempotency_guard_is_not_a_decline(self) -> None:
        """Already installed reads UP, so there is no decline to explain."""
        assert not _has_early_return(
            _fn(
                "def activate(state):\n"
                "    if state.installed is not None:\n"
                "        return\n"
                "    state.install()\n"
            )
        )

    def test_a_trailing_return_is_not_a_decline(self) -> None:
        """An unguarded return cannot skip the wiring above it."""
        assert not _has_early_return(
            _fn("def activate(state):\n    state.install()\n    return\n")
        )

    def test_a_declared_reason_is_recognised(self) -> None:
        assert _raises_declined(
            _fn(
                "def activate(state):\n"
                "    if state.store is None:\n"
                "        raise SubsystemDeclinedError('no store is wired')\n"
            )
        )

    def test_a_different_raise_is_not_a_declared_reason(self) -> None:
        assert not _raises_declined(
            _fn(
                "def activate(state):\n"
                "    if state.store is None:\n"
                "        raise ValueError('no store')\n"
            )
        )

    def test_the_error_name_in_a_message_is_not_a_declared_reason(self) -> None:
        """The type is what the reconciler reports, not the words in it.

        Matching the rendered raise expression would let any exception that
        happens to mention the name certify a decline nobody can read.
        """
        assert not _raises_declined(
            _fn(
                "def activate(state):\n"
                "    if state.store is None:\n"
                "        raise RuntimeError('SubsystemDeclinedError')\n"
            )
        )

    def test_a_qualified_raise_is_a_declared_reason(self) -> None:
        """``errors.SubsystemDeclinedError`` is the same declaration."""
        assert _raises_declined(
            _fn(
                "def activate(state):\n"
                "    if state.store is None:\n"
                "        raise errors.SubsystemDeclinedError('no store')\n"
            )
        )

    def test_a_raise_in_an_uncalled_nested_helper_declares_nothing(self) -> None:
        # The activation itself backs out silently; the reason lives in a
        # helper nothing invokes, so the reconciler still has nothing to
        # report and the gate must not be satisfied by it.
        activation = _fn(
            "def activate(state):\n"
            "    def _explain():\n"
            "        raise SubsystemDeclinedError('no store')\n"
            "    if state.store is None:\n"
            "        return\n"
            "    state.install()\n"
        )

        assert _has_early_return(activation)
        assert not _raises_declined(activation)

    def test_an_absence_guard_in_a_nested_helper_is_not_the_hosts_decline(
        self,
    ) -> None:
        # The enclosing activation installs unconditionally; the guarded
        # return belongs to a helper, so reading it as the host's own decline
        # would invent a violation.
        assert not _has_early_return(
            _fn(
                "def activate(state):\n"
                "    def _maybe(store):\n"
                "        if store is None:\n"
                "            return\n"
                "        store.warm()\n"
                "    _maybe(state.store)\n"
                "    state.install()\n"
            )
        )


class TestScan:
    def _write(self, root: Path, *, registry: str, wiring: str = "") -> None:
        """Lay out a miniature repo the scanner can walk."""
        registry_path = root / "src" / "synthorg" / "api" / "subsystems"
        registry_path.mkdir(parents=True)
        (registry_path / "registry.py").write_text(registry, encoding="utf-8")
        if wiring:
            wiring_dir = root / "src" / "synthorg" / "api"
            (wiring_dir / "wiring.py").write_text(wiring, encoding="utf-8")

    def test_a_silent_decline_is_reported(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "    state.install()\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        violations = scan_repo(tmp_path)

        assert [v.name for v in violations] == ["thing"]
        assert violations[0].activate == "wire_thing"

    def test_a_declared_settings_read_satisfies_the_rule(self, tmp_path: Path) -> None:
        """The reconciler reads those live and names the blank one itself."""
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "\n"
                "SPECS = (\n"
                "    SubsystemSpec(\n"
                "        name='thing',\n"
                "        activate=wire_thing,\n"
                "        settings=(('ns', 'key'),),\n"
                "    ),\n"
                ")\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_a_declared_reason_satisfies_the_rule(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    if state.store is None:\n"
                "        raise SubsystemDeclinedError('no store is wired')\n"
                "    state.install()\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_an_activation_that_cannot_decline_satisfies_the_rule(
        self, tmp_path: Path
    ) -> None:
        """It installs or it raises; there is no third outcome to explain."""
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    state.install()\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_a_reason_one_call_inward_counts(self, tmp_path: Path) -> None:
        """The chain is the adapter plus what it calls, not the adapter alone."""
        self._write(
            tmp_path,
            registry=(
                "from synthorg.api.wiring import install_thing\n"
                "\n"
                "def wire_thing(state):\n"
                "    install_thing(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
            wiring=(
                "def install_thing(state):\n"
                "    if state.store is None:\n"
                "        raise SubsystemDeclinedError('no store is wired')\n"
                "    state.install()\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_a_silent_decline_one_call_inward_is_reported(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            registry=(
                "from synthorg.api.wiring import install_thing\n"
                "\n"
                "def wire_thing(state):\n"
                "    install_thing(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
            wiring=(
                "def install_thing(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "    state.install()\n"
            ),
        )

        assert [v.name for v in scan_repo(tmp_path)] == ["thing"]

    def test_an_aliased_import_still_resolves(self, tmp_path: Path) -> None:
        """The callee is looked up by its own name, not by the local alias."""
        self._write(
            tmp_path,
            registry=(
                "from synthorg.api.wiring import install_thing as _install\n"
                "\n"
                "def wire_thing(state):\n"
                "    _install(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
            wiring=(
                "def install_thing(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "    state.install()\n"
            ),
        )

        assert [v.name for v in scan_repo(tmp_path)] == ["thing"]

    def test_a_decline_two_calls_inward_is_reported(self, tmp_path: Path) -> None:
        """The walk follows the whole chain, not just its first hop."""
        self._write(
            tmp_path,
            registry=(
                "from synthorg.api.wiring import install_thing\n"
                "\n"
                "def wire_thing(state):\n"
                "    install_thing(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
            wiring=(
                "def install_thing(state):\n"
                "    _build(state)\n"
                "\n"
                "def _build(state):\n"
                "    if state.store is None:\n"
                "        return\n"
                "    state.install()\n"
            ),
        )

        assert [v.name for v in scan_repo(tmp_path)] == ["thing"]

    def test_a_reason_two_calls_inward_satisfies_the_rule(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            registry=(
                "from synthorg.api.wiring import install_thing\n"
                "\n"
                "def wire_thing(state):\n"
                "    install_thing(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
            wiring=(
                "def install_thing(state):\n"
                "    _build(state)\n"
                "\n"
                "def _build(state):\n"
                "    if state.store is None:\n"
                "        raise SubsystemDeclinedError('no store is wired')\n"
                "    state.install()\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_a_recursive_chain_terminates(self, tmp_path: Path) -> None:
        """A cycle in the call graph must not hang the gate."""
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    _spin(state)\n"
                "\n"
                "def _spin(state):\n"
                "    _spin(state)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_a_called_nested_helper_still_enters_the_chain(
        self, tmp_path: Path
    ) -> None:
        # Excluding nested bodies from the host's own scan must not lose the
        # helper it actually invokes: that decline is reachable and real.
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    def _install(store):\n"
                "        if store is None:\n"
                "            return\n"
                "        store.install()\n"
                "    _install(state.store)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        assert [v.name for v in scan_repo(tmp_path)] == ["thing"]

    def test_a_reason_in_a_called_nested_helper_satisfies_the_rule(
        self, tmp_path: Path
    ) -> None:
        self._write(
            tmp_path,
            registry=(
                "def wire_thing(state):\n"
                "    def _install(store):\n"
                "        if store is None:\n"
                "            raise SubsystemDeclinedError('no store')\n"
                "        store.install()\n"
                "    _install(state.store)\n"
                "\n"
                "SPECS = (SubsystemSpec(name='thing', activate=wire_thing),)\n"
            ),
        )

        assert scan_repo(tmp_path) == ()

    def test_an_unreadable_registry_is_an_error_not_a_pass(
        self, tmp_path: Path
    ) -> None:
        """A gate that silently passes on a missing file protects nothing."""
        with pytest.raises(ValueError, match="cannot read"):
            scan_repo(tmp_path)


class TestRealRepo:
    def test_the_shipped_registry_declares_every_reason(self) -> None:
        assert main(["--repo-root", str(_REPO_ROOT)]) == 0

    def test_the_shipped_registry_declares_subsystems(self) -> None:
        """Guards the reader itself: zero specs would pass vacuously."""
        registry = ast.parse(
            (
                _REPO_ROOT / "src" / "synthorg" / "api" / "subsystems" / "registry.py"
            ).read_text(encoding="utf-8")
        )
        assert read_specs(registry)
