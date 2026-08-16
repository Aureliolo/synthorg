"""Boot-time provider-registry reload from persisted configs.

A restarted, already-set-up deployment must come back with its
DB-persisted providers live. Only ``/setup/complete`` and provider
mutations rebuild the registry, so without this boot-time reload every
provider-gated feature stays unwired after a restart.

A deployment with no providers configured and a deployment whose
providers could not be read must not collapse to the same answer: only
the first is a genuine empty company, and the second must raise, report,
and record so an operator with a full provider set is never told their
company is empty.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.provider_registry_reload import (
    reload_persisted_provider_registry,
    reload_persisted_provider_registry_for_boot,
)
from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_configs_read import (
    CoercedProviderSetting,
    ProviderConfigsRead,
    ProviderConfigsStatus,
    RejectedProviderConfig,
)
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.core.billing_enums import BillingModel
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import Notification, NotificationSeverity
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.providers.errors import ProviderConfigUnreadableError
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


def _wire_resolver(state: AppState, read: ProviderConfigsRead) -> None:
    """Wire a resolver onto *state* whose persisted read is *read*."""

    async def _get_int(namespace: str, key: str) -> int:
        msg = f"{namespace}.{key} not registered"
        raise SettingNotFoundError(msg)

    state.wire(
        SettingsStateSlice,
        config_resolver=mock_of[ConfigResolver](
            get_provider_configs_read=AsyncMock(
                spec=ConfigResolver.get_provider_configs_read,
                return_value=read,
            ),
            get_int=_get_int,
        ),
    )
    state.wire(
        IntegrationsStateSlice,
        provider_credential_catalog=mock_of[ConnectionCatalog](),
    )


def _ok(providers: dict[str, ProviderConfig]) -> ProviderConfigsRead:
    return ProviderConfigsRead(
        status=ProviderConfigsStatus.OK,
        providers=providers,
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
        _wire_resolver(
            state,
            _ok(
                {
                    "test-provider": ProviderConfig(
                        driver="scripted", connection_name="conn-scripted"
                    )
                }
            ),
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
        _wire_resolver(
            state,
            _ok(
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
        _wire_resolver(
            state,
            _ok(
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
        _wire_resolver(state, _ok({}))

        assert await reload_persisted_provider_registry(state) is None
        assert state.slice(ProvidersStateSlice).registry is None
        assert not has_active_provider(state)


class TestUnreadableIsNotEmpty:
    async def test_unreadable_config_raises_rather_than_reading_as_empty(
        self,
    ) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                rejected=(
                    RejectedProviderConfig(name="alpha", reason="driver: too short"),
                ),
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError, match="alpha"):
            await reload_persisted_provider_registry(state)

    async def test_unreadable_envelope_raises_with_its_own_detail(self) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError, match="schema_version"):
            await reload_persisted_provider_registry(state)

    async def test_partial_read_registers_the_providers_that_survived(self) -> None:
        """One rejected entry costs that entry; the rest of the org keeps running."""
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.PARTIAL,
                providers={
                    "alpha": ProviderConfig(driver="scripted", connection_name="conn-a")
                },
                rejected=(
                    RejectedProviderConfig(name="beta", reason="driver: too short"),
                ),
            ),
        )

        registry = await reload_persisted_provider_registry(state)

        assert registry is not None
        assert registry.list_providers() == ("alpha",)


class TestDiagnosticsAreRecorded:
    """A notification is a moment; the dashboard asks later."""

    async def test_a_partial_read_is_recorded_for_later_asking(self) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.PARTIAL,
                providers={
                    "alpha": ProviderConfig(driver="scripted", connection_name="conn-a")
                },
                rejected=(
                    RejectedProviderConfig(name="beta", reason="driver: too short"),
                ),
                coerced=(
                    CoercedProviderSetting(name="alpha", setting="fallback_providers"),
                ),
            ),
        )

        await reload_persisted_provider_registry(state)

        recorded = state.slice(ProvidersStateSlice).config_diagnostics
        assert recorded is not None
        assert recorded.status is ProviderConfigsStatus.PARTIAL
        assert [r.name for r in recorded.rejected] == ["beta"]
        assert [c.setting for c in recorded.coerced] == ["fallback_providers"]

    async def test_an_unreadable_read_is_recorded_before_the_raise(self) -> None:
        """Recorded first, or the raise leaves nothing able to answer why."""
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError):
            await reload_persisted_provider_registry(state)

        recorded = state.slice(ProvidersStateSlice).config_diagnostics
        assert recorded is not None
        assert recorded.status is ProviderConfigsStatus.UNREADABLE
        assert recorded.detail == "schema_version: Field required"


class TestOperatorIsNotified:
    @staticmethod
    def _dispatcher() -> tuple[NotificationDispatcher, list[Notification]]:
        sent: list[Notification] = []

        async def _dispatch(notification: Notification) -> int:
            sent.append(notification)
            return 1

        return mock_of[NotificationDispatcher](dispatch=_dispatch), sent

    async def test_a_partial_read_notifies_at_warning_naming_what_was_lost(
        self,
    ) -> None:
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.PARTIAL,
                providers={
                    "alpha": ProviderConfig(driver="scripted", connection_name="conn-a")
                },
                rejected=(
                    RejectedProviderConfig(name="beta", reason="driver: too short"),
                ),
            ),
        )

        await reload_persisted_provider_registry(state)

        assert len(sent) == 1
        assert sent[0].severity is NotificationSeverity.WARNING
        assert "beta" in sent[0].body

    async def test_an_unreadable_read_notifies_at_error_with_the_envelope_detail(
        self,
    ) -> None:
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError):
            await reload_persisted_provider_registry(state)

        assert len(sent) == 1
        assert sent[0].severity is NotificationSeverity.ERROR
        assert "schema_version" in sent[0].body

    async def test_the_envelope_branch_says_what_stays_unavailable(self) -> None:
        """An operator reads this sentence, so it needs a subject.

        The rejected-entry branch can say "They", meaning the connections
        it just named. This branch names none, so the same continuation
        would point at nothing.
        """
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError):
            await reload_persisted_provider_registry(state)

        assert "They stay unavailable" not in sent[0].body
        assert "No provider is available" in sent[0].body

    async def test_a_blank_keyed_entry_never_renders_the_word_none(self) -> None:
        """The one shape that has neither a name nor an envelope detail.

        A blob keyed with a blank name is rejected under that empty name,
        so there is nothing to list, and the entries were read one by one,
        so there is no envelope detail either. Interpolating the absent
        detail put the literal "None" in front of an operator.
        """
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                rejected=(
                    RejectedProviderConfig(
                        name="",
                        reason="provider name is blank, so nothing can be bound to it",
                    ),
                ),
            ),
        )

        with pytest.raises(ProviderConfigUnreadableError):
            await reload_persisted_provider_registry(state)

        assert "None" not in sent[0].body
        assert "nothing can be bound to it" in sent[0].body

    async def test_the_envelope_failure_is_logged_once_per_reload(self) -> None:
        """The reader no longer logs it, so this is the only place it lands.

        Names no provider, because an envelope nothing could be made of
        has no entry to blame.
        """
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
                reason="unknown_schema_version",
            ),
        )

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(ProviderConfigUnreadableError),
        ):
            await reload_persisted_provider_registry(state)

        unreadable = [
            log for log in logs if log.get("event") == "provider.config.unreadable"
        ]
        assert len(unreadable) == 1
        assert unreadable[0]["reason"] == "unknown_schema_version"
        assert unreadable[0]["detail"] == "schema_version: Field required"

    async def test_a_clean_read_notifies_nobody(self) -> None:
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            _ok({"alpha": ProviderConfig(driver="scripted", connection_name="conn-a")}),
        )

        await reload_persisted_provider_registry(state)

        assert sent == []

    async def test_a_coercion_alone_notifies_nobody(self) -> None:
        """A stripped retired setting is inert; a notification every restart is not."""
        state = _make_state()
        dispatcher, sent = self._dispatcher()
        state.wire(NotificationsStateSlice, dispatcher=dispatcher)
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.OK,
                providers={
                    "alpha": ProviderConfig(driver="scripted", connection_name="conn-a")
                },
                coerced=(
                    CoercedProviderSetting(name="alpha", setting="fallback_providers"),
                ),
            ),
        )

        await reload_persisted_provider_registry(state)

        assert sent == []

    async def test_a_failing_sink_does_not_stop_the_reload(self) -> None:
        """The conditions are already logged; a sink must not decide the boot."""
        state = _make_state()

        async def _dispatch(notification: Notification) -> int:
            msg = "sink is down"
            raise RuntimeError(msg)

        state.wire(
            NotificationsStateSlice,
            dispatcher=mock_of[NotificationDispatcher](dispatch=_dispatch),
        )
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.PARTIAL,
                providers={
                    "alpha": ProviderConfig(driver="scripted", connection_name="conn-a")
                },
                rejected=(
                    RejectedProviderConfig(name="beta", reason="driver: too short"),
                ),
            ),
        )

        registry = await reload_persisted_provider_registry(state)

        assert registry is not None
        assert registry.list_providers() == ("alpha",)


class TestBootPosture:
    """Boot serves without providers; setup refuses. Two callers, two postures."""

    async def test_boot_serves_without_providers_instead_of_raising(self) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        assert await reload_persisted_provider_registry_for_boot(state) is None
        assert state.slice(ProvidersStateSlice).registry is None

    async def test_boot_still_records_why_it_has_no_providers(self) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            ProviderConfigsRead(
                status=ProviderConfigsStatus.UNREADABLE,
                providers={},
                detail="schema_version: Field required",
            ),
        )

        await reload_persisted_provider_registry_for_boot(state)

        recorded = state.slice(ProvidersStateSlice).config_diagnostics
        assert recorded is not None
        assert recorded.status is ProviderConfigsStatus.UNREADABLE

    async def test_boot_returns_the_registry_when_the_config_reads(self) -> None:
        state = _make_state()
        _wire_resolver(
            state,
            _ok({"alpha": ProviderConfig(driver="scripted", connection_name="conn-a")}),
        )

        registry = await reload_persisted_provider_registry_for_boot(state)

        assert registry is not None
        assert registry.list_providers() == ("alpha",)
