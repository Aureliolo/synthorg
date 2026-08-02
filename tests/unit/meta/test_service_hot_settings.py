"""Hot-reload coverage for the self-improvement meta-loop.

Asserts that the ``self_improvement`` master switch, the strategy toggles,
and the analysis-model seam take effect at runtime through a live
``ConfigResolver`` without rebuilding the service, and that an in-flight
cycle reads each toggle once (captured-reference semantics).
"""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.config.schema import RootConfig
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.meta.appliers.config_applier import SettingsWritePort
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import ImprovementProposal, ProposalAltitude
from synthorg.meta.service import SelfImprovementService
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.write_governance import SettingsWriteGovernance
from tests._shared.fake_clock import FakeClock
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.meta.test_service import _snap

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    """A settings service over a connected in-memory backend."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _resolver(settings: SettingsService) -> ConfigResolver:
    return ConfigResolver(
        settings_service=settings, config=RootConfig(company_name="test")
    )


def _svc(
    settings: SettingsService,
    *,
    config_tuning: bool = True,
    architecture: bool = False,
    prompt_tuning: bool = False,
    memory_backend: InMemoryBackend | None = None,
) -> SelfImprovementService:
    cfg = SelfImprovementConfig(
        enabled=True,
        config_tuning_enabled=config_tuning,
        architecture_proposals_enabled=architecture,
        prompt_tuning_enabled=prompt_tuning,
    )
    return SelfImprovementService(
        config=cfg,
        clock=FakeClock(),
        memory_backend=memory_backend,
        approval_store=AsyncMock(spec=ApprovalStoreProtocol),
        settings_writer=AsyncMock(spec=SettingsWritePort),
        config_resolver=_resolver(settings),
    )


async def _enable_loop(settings: SettingsService) -> None:
    """Turn both per-cycle master gates on so a cycle proceeds."""
    await settings.set("engine", "evolution_enabled", "true")
    await settings.set("self_improvement", "enabled", "true")


def _adjuster(svc: SelfImprovementService) -> object:
    """Read the lazily-built confidence adjuster (un-narrowed)."""
    return svc._confidence_adjuster


async def test_enabled_gate_is_live_per_cycle(settings: SettingsService) -> None:
    """``self_improvement.enabled`` gates the cycle live, no restart."""
    svc = _svc(settings)
    await _enable_loop(settings)
    assert await svc.run_cycle(_snap(quality=4.0))  # both gates on -> proposes

    await settings.set("self_improvement", "enabled", "false")
    assert await svc.run_cycle(_snap(quality=4.0)) == ()  # gated off live

    await settings.set("self_improvement", "enabled", "true")
    assert await svc.run_cycle(_snap(quality=4.0))  # back on, same instance


async def test_strategy_toggle_is_live_per_cycle(settings: SettingsService) -> None:
    """An architecture toggle flips which altitude runs on the next cycle."""
    svc = _svc(settings, config_tuning=True, architecture=False)
    await _enable_loop(settings)

    # coord_ratio=0.5 targets both config-tuning and architecture.
    first = await svc.run_cycle(_snap(coord_ratio=0.5))
    altitudes = {p.altitude for p in first}
    assert ProposalAltitude.CONFIG_TUNING in altitudes
    assert ProposalAltitude.ARCHITECTURE not in altitudes

    await settings.set("self_improvement", "architecture_proposals_enabled", "true")
    second = await svc.run_cycle(_snap(coord_ratio=0.5))
    assert ProposalAltitude.ARCHITECTURE in {p.altitude for p in second}


async def test_toggles_snapshotted_once_per_cycle(settings: SettingsService) -> None:
    """Each strategy toggle is read once per cycle (captured reference).

    A single ``run_cycle`` resolves the enabled-altitude set once before
    dispatch, so a concurrent settings swap cannot change the set mid-cycle.
    """
    svc = _svc(settings, config_tuning=True, architecture=True, prompt_tuning=True)
    await _enable_loop(settings)
    resolver = svc._config_resolver
    assert resolver is not None

    with patch.object(resolver, "get_bool", wraps=resolver.get_bool) as spy:
        # coord_ratio targets multiple altitudes, so several strategies run.
        await svc.run_cycle(_snap(coord_ratio=0.5))

    arch_reads = [
        call
        for call in spy.await_args_list
        if call.args == ("self_improvement", "architecture_proposals_enabled")
    ]
    assert len(arch_reads) == 1


async def test_learning_lazily_activates_on_runtime_enable(
    settings: SettingsService,
) -> None:
    """Learning builds its adjuster lazily on the first cycle after enable."""
    backend = InMemoryBackend()
    await backend.connect()
    try:
        svc = _svc(settings, memory_backend=backend)
        await _enable_loop(settings)

        # Off by default: no adjuster, and a cycle leaves it unbuilt. Read into
        # locals so attribute narrowing does not leak across the assertions.
        assert _adjuster(svc) is None
        await svc.run_cycle(_snap(quality=4.0))
        assert _adjuster(svc) is None

        # Enable learning + the persona master; the next cycle builds it lazily.
        await settings.set("self_improvement", "chief_of_staff_enabled", "true")
        await settings.set("chief_of_staff", "learning_enabled", "true")
        await svc.run_cycle(_snap(quality=4.0))
        assert _adjuster(svc) is not None
    finally:
        await backend.disconnect()


async def test_learning_without_backend_warns_once(
    settings: SettingsService,
) -> None:
    """A persistently backend-less learning cap warns once, not every cycle."""
    svc = _svc(settings, memory_backend=None)
    await _enable_loop(settings)
    await settings.set("self_improvement", "chief_of_staff_enabled", "true")
    await settings.set("chief_of_staff", "learning_enabled", "true")

    with patch("synthorg.meta._service_live_config.logger") as mock_logger:
        await svc.run_cycle(_snap(quality=4.0))
        await svc.run_cycle(_snap(quality=4.0))

    assert mock_logger.warning.call_count == 1
    assert _adjuster(svc) is None


async def test_learning_master_gate_blocks_without_persona(
    settings: SettingsService,
) -> None:
    """``learning_enabled`` alone does not activate learning without master."""
    backend = InMemoryBackend()
    await backend.connect()
    try:
        svc = _svc(settings, memory_backend=backend)
        await _enable_loop(settings)
        await settings.set("chief_of_staff", "learning_enabled", "true")
        # Master (chief_of_staff_enabled) stays off.

        await svc.run_cycle(_snap(quality=4.0))
        assert _adjuster(svc) is None
    finally:
        await backend.disconnect()


async def test_a_running_service_never_grows_code_modification(
    settings: SettingsService,
) -> None:
    """Turning the flag on does not arm an already-built service.

    The strategy and applier are chosen when the service is constructed from
    a config the credential check has already validated. Writing the flag
    afterwards therefore adds neither to this instance: the capability
    arrives with the next service built from the new config, not by mutating
    one that was built without it.
    """
    svc = _svc(settings)  # code_modification_enabled=False baked, no provider
    await _enable_loop(settings)
    await settings.set(
        "self_improvement",
        "code_modification_enabled",
        "true",
        governance=SettingsWriteGovernance(confirm=True, reason="test", actor="admin"),
    )

    assert all(
        s.altitude is not ProposalAltitude.CODE_MODIFICATION for s in svc._strategies
    )
    assert ProposalAltitude.CODE_MODIFICATION not in svc._appliers


async def test_analysis_settings_model_is_live(settings: SettingsService) -> None:
    """``analysis_model`` resolves live with a baked fallback."""
    svc = _svc(settings)

    baked = await svc.resolve_analysis_settings()
    assert baked.llm_model == SelfImprovementConfig().analysis_model

    await settings.set(
        "self_improvement",
        "analysis_model",
        serialize_model_ref(
            ModelRef(provider="example-provider", model_id="live-analysis-001")
        ),
    )
    live = await svc.resolve_analysis_settings()
    assert live.llm_model == "live-analysis-001"
    # Sampling parameters stay baked (blob-only, not registered settings).
    assert live.temperature == SelfImprovementConfig().analysis_temperature
    assert live.max_tokens == SelfImprovementConfig().analysis_max_tokens


async def test_concurrent_swap_does_not_corrupt_in_flight_cycle(
    settings: SettingsService,
) -> None:
    """A settings swap during a cycle does not change that cycle's snapshot.

    The cycle resolves its enabled-altitude set once before dispatch; a
    concurrent ``settings.set`` that lands while a strategy is mid-dispatch
    must not retroactively drop an altitude the cycle already captured, so the
    architecture strategy still dispatches its proposal this cycle.
    """
    svc = _svc(settings, config_tuning=True, architecture=True)
    await _enable_loop(settings)
    # The live read tracks the settings store (architecture is off by default),
    # so turn it on explicitly before the cycle captures its snapshot.
    await settings.set("self_improvement", "architecture_proposals_enabled", "true")
    entered = asyncio.Event()
    release = asyncio.Event()
    dispatched: list[ImprovementProposal] = []
    original = svc._dispatch_strategies

    async def slow_dispatch(
        snapshot: object, matches: object, strategies: object
    ) -> list[ImprovementProposal]:
        entered.set()
        await release.wait()
        result = await original(snapshot, matches, strategies)  # type: ignore[arg-type]
        dispatched.extend(result)
        return result

    with patch.object(svc, "_dispatch_strategies", slow_dispatch):
        cycle = asyncio.ensure_future(svc.run_cycle(_snap(coord_ratio=0.5)))
        await entered.wait()
        # Land the swap after the cycle captured its altitude snapshot.
        await settings.set(
            "self_improvement", "architecture_proposals_enabled", "false"
        )
        release.set()
        await cycle

    # The in-flight cycle dispatched the architecture strategy from its captured
    # (pre-swap) snapshot, even though architecture was disabled mid-cycle.
    assert ProposalAltitude.ARCHITECTURE in {p.altitude for p in dispatched}
    # A fresh cycle reflects the swap: architecture no longer dispatches.
    dispatched.clear()
    with patch.object(svc, "_dispatch_strategies", slow_dispatch):
        release.set()
        await svc.run_cycle(_snap(coord_ratio=0.5))
    assert ProposalAltitude.ARCHITECTURE not in {p.altitude for p in dispatched}
