"""Boot-time provider-registry reload from persisted configs.

A restarted, already-set-up deployment must come back with its
DB-persisted providers live. Only ``/setup/complete`` and provider
mutations rebuild the registry, so without this boot-time reload every
provider-gated feature stays unwired after a restart.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.provider_registry_reload import (
    reload_persisted_provider_registry,
)
from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.providers.state import ProvidersStateSlice, has_active_provider
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_state() -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
    )


def _resolver(configs: dict[str, ProviderConfig]) -> ConfigResolver:
    async def _get_provider_configs() -> dict[str, ProviderConfig]:
        return configs

    async def _get_int(namespace: str, key: str) -> int:
        msg = f"{namespace}.{key} not registered"
        raise SettingNotFoundError(msg)

    async def _get_str(namespace: str, key: str) -> str:
        msg = f"{namespace}.{key} not registered"
        raise SettingNotFoundError(msg)

    return mock_of[ConfigResolver](  # type: ignore[no-any-return]
        get_provider_configs=_get_provider_configs,
        get_int=_get_int,
        get_str=_get_str,
    )


def _cost_record(provider: str) -> CostRecord:
    return CostRecord(
        agent_id=NotBlankStr("agent-1"),
        provider=NotBlankStr(provider),
        model=NotBlankStr("example-basic-001"),
        input_tokens=10,
        output_tokens=5,
        cost=0.0,
        currency=DEFAULT_CURRENCY,
        timestamp=datetime(2026, 8, 10, tzinfo=UTC),
    )


class TestReloadPersistedProviderRegistry:
    async def test_swaps_registry_from_persisted_configs(self) -> None:
        state = _make_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=_resolver(
                {
                    "test-provider": ProviderConfig(
                        driver="scripted", connection_name="conn-scripted"
                    )
                }
            ),
        )
        state.wire(
            IntegrationsStateSlice,
            provider_credential_catalog=mock_of[ConnectionCatalog](),
        )

        registry = await reload_persisted_provider_registry(state)

        assert registry is not None
        assert state.slice(ProvidersStateSlice).registry is registry
        assert has_active_provider(state)
        assert registry.list_providers() == ("test-provider",)

    async def test_every_persisted_connection_is_reachable_by_name(self) -> None:
        """The reload registers each persisted connection under its own name.

        There is no house connection to nominate: a feature reaches the one
        its own ``(provider, model)`` pair names, so what the cold boot owes
        is that every persisted connection answers to its name.
        """
        state = _make_state()
        state.wire(
            SettingsStateSlice,
            config_resolver=_resolver(
                {
                    "test-provider": ProviderConfig(
                        driver="scripted", connection_name="conn-scripted"
                    ),
                    "test-provider-2": ProviderConfig(
                        driver="scripted", connection_name="conn-scripted-2"
                    ),
                },
            ),
        )
        state.wire(
            IntegrationsStateSlice,
            provider_credential_catalog=mock_of[ConnectionCatalog](),
        )

        registry = await reload_persisted_provider_registry(state)

        assert registry is not None
        assert registry.list_providers() == ("test-provider", "test-provider-2")
        assert registry.get("test-provider") is not registry.get("test-provider-2")

    async def test_the_ledger_learns_how_each_connection_charges(self) -> None:
        """The reload binds the billing snapshot the ledger stamps from.

        Left unbound, every recorded call keeps ``UNKNOWN``, which reads as
        unmeasurable and blanks the money percentage on a perfectly metered
        estate. Bound on the same pass that installs the registry, so it
        cannot lag the configs it was built from.
        """
        state = _make_state()
        tracker = CostTracker()
        state.wire(BudgetStateSlice, cost_tracker=tracker)
        state.wire(
            SettingsStateSlice,
            config_resolver=_resolver(
                {
                    "metered": ProviderConfig(
                        driver="scripted",
                        connection_name="conn-metered",
                        billing_model=BillingModel.PER_TOKEN,
                    ),
                    "flat-gateway": ProviderConfig(
                        driver="scripted",
                        connection_name="conn-flat",
                        billing_model=BillingModel.FLAT_RATE,
                    ),
                },
            ),
        )
        state.wire(
            IntegrationsStateSlice,
            provider_credential_catalog=mock_of[ConnectionCatalog](),
        )

        await reload_persisted_provider_registry(state)

        await tracker.record(_cost_record("flat-gateway"))
        await tracker.record(_cost_record("metered"))
        stored = {r.provider: r.billing_model for r in await tracker.get_records()}
        assert stored == {
            "flat-gateway": BillingModel.FLAT_RATE,
            "metered": BillingModel.PER_TOKEN,
        }

    async def test_no_resolver_is_a_noop(self) -> None:
        state = _make_state()
        assert await reload_persisted_provider_registry(state) is None
        assert state.slice(ProvidersStateSlice).registry is None

    async def test_no_persisted_configs_is_a_noop(self) -> None:
        """A genuine first-run empty company keeps the empty-company boot."""
        state = _make_state()
        state.wire(SettingsStateSlice, config_resolver=_resolver({}))

        assert await reload_persisted_provider_registry(state) is None
        assert state.slice(ProvidersStateSlice).registry is None
        assert not has_active_provider(state)
