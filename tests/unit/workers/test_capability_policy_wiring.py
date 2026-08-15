"""Tests for the one-instance guarantee on the capability policy.

Two answers to "what rung does this agent run at" is the defect the whole
policy exists to prevent, and the memo on the engine slice is what makes one
instance true. The memo is read, several awaits happen, and only then is it
written, so the guarantee holds only while nothing else reaches the same
window: the runtime reload and the subsystem reconciler serialise their own
work and know nothing of each other, and the reconciler's periodic resync
fires forever.
"""

import asyncio
from typing import Any

import pytest

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.engine.state import EngineStateSlice
from synthorg.workers import _capability_assignment_wiring
from synthorg.workers._capability_policy_wiring import build_capability_policy
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _state_with_a_provider() -> Any:  # type: ignore[explicit-any]  # AppState is assembled from heterogeneous slices
    """Return an app state carrying one provider, so the build proceeds.

    Returns:
        The app state.
    """
    return make_app_state(
        config=RootConfig(
            company_name="test",
            providers={
                "test-provider": ProviderConfig(
                    connection_name="conn-test",
                    models=(ProviderModelConfig(id="example-capable-001"),),
                )
            },
        )
    )


async def test_concurrent_builds_yield_one_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second caller entering the window takes what the first wired.

    Both callers would otherwise read an empty memo, build separately, and
    race to the slice. Every consumer holds whichever instance its own build
    returned, while the settings subscriber can only ever re-point the one
    that landed, so an operator edit reaches some consumers and not others.
    """
    state = _state_with_a_provider()
    builds = 0
    real = _capability_assignment_wiring.build_capability_assignment_service

    async def _counted(app_state: Any) -> Any:  # type: ignore[explicit-any]  # mirrors the patched builder's own signature
        nonlocal builds
        builds += 1
        # Hand control back inside the window the memo does not cover, which
        # is what a second caller needs to enter it.
        await asyncio.sleep(0)
        return await real(app_state)

    # Patched on the defining module, because the wiring imports it inside the
    # function: patching the consumer would silently do nothing.
    monkeypatch.setattr(
        _capability_assignment_wiring,
        "build_capability_assignment_service",
        _counted,
    )

    first, second = await asyncio.gather(
        build_capability_policy(state), build_capability_policy(state)
    )

    assert first is not None
    assert first is second
    assert first is state.slice(EngineStateSlice).capability_policy
    assert builds == 1


async def test_a_later_call_reuses_the_wired_policy() -> None:
    """The memo still short-circuits once the policy is on the slice."""
    state = _state_with_a_provider()

    first = await build_capability_policy(state)
    second = await build_capability_policy(state)

    assert first is not None
    assert first is second


async def test_no_providers_yields_no_policy() -> None:
    """Nothing to grade, so there is nothing to judge against."""
    state = make_app_state(config=RootConfig(company_name="test"))

    assert await build_capability_policy(state) is None
    assert state.slice(EngineStateSlice).capability_policy is None
