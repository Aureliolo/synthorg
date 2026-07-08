"""Tests for the model-refresh orchestration service."""

from unittest.mock import AsyncMock

import pytest

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.live_discovery_probe import (
    LiveCatalogReport,
    LiveDiscoveryProbe,
)
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _model(model_id: str, *, generation: float) -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        metadata=ModelMetadata(family="fam", generation=generation),
    )


_OLD = _model("old", generation=1.0)
_NEW = _model("new", generation=2.0)
_PROVIDER = ProviderConfig(
    connection_name="conn-test", base_url="http://localhost:11434", models=(_OLD, _NEW)
)
# A default consumer pinned to the old model, so the old->new upgrade is
# usage-backed: the recommender only surfaces upgrades for models an agent
# actually runs, so a service with no agents would otherwise recommend nothing.
_DEFAULT_AGENT = AgentConfig(
    name="default-consumer",
    role="Worker",
    department="Engineering",
    model={"provider": "example-provider", "model_id": "old"},
)


def _build_service(
    *,
    repo: UpgradeRecommendationRepository,
    agents: tuple[AgentConfig, ...] = (_DEFAULT_AGENT,),
    probe: LiveDiscoveryProbe | None = None,
    notification_dispatcher: NotificationDispatcher | None = None,
) -> ModelRefreshService:
    probe = probe or mock_of[LiveDiscoveryProbe](
        discover_report=AsyncMock(
            return_value=LiveCatalogReport(
                provider_name="example-provider",
                discovered=(_OLD, _NEW),
                checked_ids=("old", "new"),
            ),
        ),
    )
    mgmt = mock_of[ProviderManagementService](
        list_providers=AsyncMock(return_value={"example-provider": _PROVIDER}),
        add_model=AsyncMock(),
        flag_models_stale=AsyncMock(),
        get_provider=AsyncMock(return_value=_PROVIDER),
    )
    resolver = mock_of[ConfigResolver](get_agents=AsyncMock(return_value=agents))
    return ModelRefreshService(
        mgmt_service=mgmt,
        probe=probe,
        recommender=UpgradeRecommender(),
        repo=repo,
        config_resolver=resolver,
        notification_dispatcher=notification_dispatcher,
        clock=FakeClock(),
    )


class TestModelRefreshService:
    async def test_off_mode_returns_empty_report(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository]()
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.OFF)
        assert report.providers_scanned == 0
        assert report.recommended_count == 0

    async def test_reconcile_persists_recommendation_with_pinned_agents(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        agent = AgentConfig(
            name="writer",
            role="Writer",
            department="Engineering",
            model={"provider": "example-provider", "model_id": "old"},
        )
        service = _build_service(repo=repo, agents=(agent,))
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 1
        repo.save.assert_awaited_once()
        stored = repo.save.await_args.args[0]
        assert isinstance(stored, StoredUpgradeRecommendation)
        assert stored.status is RecommendationStatus.PENDING
        assert stored.agent_ids == ("writer",)

    async def test_dedup_skips_existing_pending(self) -> None:
        existing = StoredUpgradeRecommendation(
            recommendation=UpgradeRecommendation(
                provider_name="example-provider",
                current_model_id="old",
                recommended_model_id="new",
                family="fam",
                current_generation=1.0,
                recommended_generation=2.0,
                score=0.5,
                reason="x",
            ),
            created_at=FakeClock().now(),
        )
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(existing,)),
            save=AsyncMock(),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 0
        repo.save.assert_not_called()

    async def test_unused_model_yields_no_recommendation(self) -> None:
        # The catalogue has an old->new upgrade, but no agent runs the old
        # model, so there is nothing to reassign: no recommendation is produced.
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        service = _build_service(repo=repo, agents=())
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 0
        repo.save.assert_not_called()

    async def test_pending_for_unused_model_is_superseded(self) -> None:
        # A pending rec whose current model no longer has any consumer is
        # retired: the recommender stops producing it once it is unused.
        orphan = StoredUpgradeRecommendation(
            recommendation=UpgradeRecommendation(
                provider_name="example-provider",
                current_model_id="old",
                recommended_model_id="new",
                family="fam",
                current_generation=1.0,
                recommended_generation=2.0,
                score=0.5,
                reason="x",
            ),
            created_at=FakeClock().now(),
        )
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(orphan,)),
            save=AsyncMock(),
            transition_if=AsyncMock(return_value=True),
        )
        service = _build_service(repo=repo, agents=())
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 0
        assert report.superseded_count == 1
        assert repo.transition_if.await_args.kwargs["to_state"] is (
            RecommendationStatus.SUPERSEDED
        )

    @staticmethod
    def _obsolete_pending() -> StoredUpgradeRecommendation:
        # A pending row the recommender no longer produces: its current model
        # ("ancient") is not in the provider catalogue, so the reconcile's
        # authoritative set (old->new) never re-surfaces it.
        return StoredUpgradeRecommendation(
            recommendation=UpgradeRecommendation(
                provider_name="example-provider",
                current_model_id="ancient",
                recommended_model_id="new",
                family="fam",
                current_generation=0.5,
                recommended_generation=2.0,
                score=0.5,
                reason="stale pick",
            ),
            created_at=FakeClock().now(),
        )

    async def test_obsolete_pending_is_superseded(self) -> None:
        obsolete = self._obsolete_pending()
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(obsolete,)),
            save=AsyncMock(),
            transition_if=AsyncMock(return_value=True),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        # The recommender still produces old->new (persisted anew) ...
        assert report.recommended_count == 1
        # ... and the obsolete ancient->new pending row is retired.
        assert report.superseded_count == 1
        repo.transition_if.assert_awaited_once()
        call = repo.transition_if.await_args
        assert call.args[0] == obsolete.id
        assert call.kwargs["from_state"] is RecommendationStatus.PENDING
        assert call.kwargs["to_state"] is RecommendationStatus.SUPERSEDED
        assert call.kwargs["decided_by"] == "reconcile"

    async def test_detect_only_does_not_supersede(self) -> None:
        obsolete = self._obsolete_pending()
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(obsolete,)),
            save=AsyncMock(),
            transition_if=AsyncMock(return_value=True),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.DETECT_ONLY)
        # Detect-only produces no recommendations; its empty set must NOT be
        # read as "retire everything".
        assert report.superseded_count == 0
        repo.transition_if.assert_not_called()

    @staticmethod
    def _removed_provider_pending() -> StoredUpgradeRecommendation:
        # A pending row for a provider dropped from configuration entirely:
        # the per-provider reconcile loop never visits it, so only the
        # post-loop cleanup can retire it.
        return StoredUpgradeRecommendation(
            recommendation=UpgradeRecommendation(
                provider_name="removed-provider",
                current_model_id="old",
                recommended_model_id="new",
                family="fam",
                current_generation=1.0,
                recommended_generation=2.0,
                score=0.5,
                reason="orphaned pick",
            ),
            created_at=FakeClock().now(),
        )

    async def test_pending_for_removed_provider_is_superseded(self) -> None:
        orphan = self._removed_provider_pending()
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(orphan,)),
            save=AsyncMock(),
            transition_if=AsyncMock(return_value=True),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        # The configured provider still yields old->new, and the orphaned row
        # whose provider no longer exists is retired despite never being
        # visited by the per-provider loop.
        assert report.recommended_count == 1
        assert report.superseded_count == 1
        repo.transition_if.assert_awaited_once()
        call = repo.transition_if.await_args
        assert call.args[0] == orphan.id
        assert call.kwargs["to_state"] is RecommendationStatus.SUPERSEDED

    async def test_removed_provider_pending_not_superseded_in_detect_only(
        self,
    ) -> None:
        orphan = self._removed_provider_pending()
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(orphan,)),
            save=AsyncMock(),
            transition_if=AsyncMock(return_value=True),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.DETECT_ONLY)
        # A detect-only pass must not retire orphaned rows either.
        assert report.superseded_count == 0
        repo.transition_if.assert_not_called()

    async def test_auto_apply_invokes_hook(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        service = _build_service(repo=repo)
        applied: list[StoredUpgradeRecommendation] = []

        async def _hook(stored: StoredUpgradeRecommendation) -> None:
            applied.append(stored)

        report = await service.run_cycle(
            mode=RefreshMode.RECONCILE_RECOMMEND,
            auto_apply=True,
            apply_recommendation=_hook,
        )
        assert report.auto_applied_count == 1
        assert len(applied) == 1

    async def test_stale_configured_model_dispatches_operator_alert(self) -> None:
        """A configured model no longer served fires a HEALTH/WARNING alert."""
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        # The live catalogue no longer advertises "old", so it is stale.
        stale_probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="example-provider",
                    discovered=(_NEW,),
                    missing_ids=("old",),
                    checked_ids=("old", "new"),
                ),
            ),
        )
        dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
        service = _build_service(
            repo=repo,
            probe=stale_probe,
            notification_dispatcher=dispatcher,
        )
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        dispatcher.dispatch.assert_awaited_once()
        note = dispatcher.dispatch.await_args.args[0]
        assert isinstance(note, Notification)
        assert note.category is NotificationCategory.HEALTH
        assert note.severity is NotificationSeverity.WARNING
        assert "old" in note.body

    async def test_persistently_stale_model_alerts_once_across_cycles(self) -> None:
        """A stale model persisting across cycles alerts once, not every cycle."""
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        stale_probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="example-provider",
                    discovered=(_NEW,),
                    missing_ids=("old",),
                    checked_ids=("old", "new"),
                ),
            ),
        )
        dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
        service = _build_service(
            repo=repo,
            probe=stale_probe,
            notification_dispatcher=dispatcher,
        )
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        # The same stale model in the second cycle is deduped, not re-alerted.
        dispatcher.dispatch.assert_awaited_once()

    async def test_healed_then_restale_model_alerts_again(self) -> None:
        """A model that recovers and later goes stale again alerts afresh."""
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        stale = LiveCatalogReport(
            provider_name="example-provider",
            discovered=(_NEW,),
            missing_ids=("old",),
            checked_ids=("old", "new"),
        )
        healed = LiveCatalogReport(
            provider_name="example-provider",
            discovered=(_NEW,),
            missing_ids=(),
            checked_ids=("old", "new"),
        )
        probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(side_effect=[stale, healed, stale]),
        )
        dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
        service = _build_service(
            repo=repo,
            probe=probe,
            notification_dispatcher=dispatcher,
        )
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)  # stale: alert
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)  # healed: prune
        await service.run_cycle(
            mode=RefreshMode.RECONCILE_RECOMMEND
        )  # stale: alert again
        assert dispatcher.dispatch.await_count == 2

    async def test_no_stale_models_dispatches_nothing(self) -> None:
        """A clean cycle (no stale models) raises no operator alert."""
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
        service = _build_service(repo=repo, notification_dispatcher=dispatcher)
        await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        dispatcher.dispatch.assert_not_called()

    async def test_provider_failure_isolated(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        # A probe that errors makes the per-provider reconcile raise; the
        # failing probe is injected at construction, not patched onto a
        # private attribute.
        failing_probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(side_effect=RuntimeError("boom")),
        )
        service = _build_service(repo=repo, probe=failing_probe)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        # The provider was attempted (counted) but its reconcile failed,
        # so no recommendation was produced.
        assert report.providers_scanned == 1
        assert report.recommended_count == 0
