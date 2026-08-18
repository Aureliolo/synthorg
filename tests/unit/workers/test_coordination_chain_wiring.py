"""_build_coordination_chain gates and composes the coordination pipeline."""

import pytest

from synthorg.core.middleware_config import DEFAULT_COORDINATION_CHAIN
from synthorg.workers._coordinator_assembly import _build_coordination_chain

pytestmark = pytest.mark.unit


class TestBuildCoordinationChain:
    def test_disabled_returns_none(self) -> None:
        assert _build_coordination_chain(enabled=False) is None

    def test_enabled_builds_full_default_chain(self) -> None:
        chain = _build_coordination_chain(enabled=True)
        assert chain is not None
        assert chain.names == DEFAULT_COORDINATION_CHAIN

    def test_chain_carries_no_stall_authority(self) -> None:
        """No middleware here decides whether a run is stuck.

        That question has two owners with the evidence to answer it: the
        execution loop's stagnation detector, which sees the turns, and
        the initiative rollup's ``stall_reason``, which derives it from
        persisted item status. A wave-level third opinion announced a
        verdict nobody could act on.
        """
        chain = _build_coordination_chain(enabled=True)
        assert chain is not None
        assert not any("replan" in name or "progress" in name for name in chain.names)
