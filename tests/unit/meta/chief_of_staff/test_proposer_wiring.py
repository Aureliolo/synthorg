"""Unit tests for the proposer build + persistence-factory wiring."""

import pytest

from synthorg.api.app_builders import build_chief_of_staff_proposer
from synthorg.api.approval_store import ApprovalStore
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.persistence.conversational_factory import (
    ConversationalRepositories,
    build_conversational_repositories,
)
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


class _FakeRegistry:
    """Minimal ``ProviderRegistry`` surface used by the builder."""

    def __init__(self, *, providers: list[str]) -> None:
        self._providers = providers
        self._provider = ScriptedProvider(responses=[])

    def list_providers(self) -> list[str]:
        return self._providers

    def get(self, name: str) -> ScriptedProvider:
        del name
        return self._provider


def _repos() -> ConversationalRepositories:
    # The builder only stores these references; behaviour is covered by
    # the proposer + conformance suites, so opaque sentinels suffice.
    return ConversationalRepositories(
        conversation_repo=object(),  # type: ignore[arg-type]
        turn_repo=object(),  # type: ignore[arg-type]
        proposal_repo=object(),  # type: ignore[arg-type]
        participant_repo=object(),  # type: ignore[arg-type]
        invite_repo=object(),  # type: ignore[arg-type]
    )


class TestBuildChiefOfStaffProposer:
    def test_none_when_disabled(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=False),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert result is None

    def test_none_when_no_repositories(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=None,
            cost_tracker=None,
        )
        assert result is None

    def test_none_when_no_providers(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True),
            provider_registry=_FakeRegistry(providers=[]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert result is None

    def test_builds_when_all_present(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert isinstance(result, ChiefOfStaffProposer)


class TestBuildConversationalRepositories:
    def test_none_when_backend_absent(self) -> None:
        assert build_conversational_repositories(None) is None

    def test_none_when_not_connected(self) -> None:
        class _Disconnected:
            is_connected = False
            backend_name = "sqlite"

            def get_db(self) -> object:
                return object()

        assert (
            build_conversational_repositories(_Disconnected())  # type: ignore[arg-type]
            is None
        )

    def test_none_when_unknown_backend(self) -> None:
        class _Unknown:
            is_connected = True
            backend_name = "mysql"

            def get_db(self) -> object:
                return object()

        assert (
            build_conversational_repositories(_Unknown())  # type: ignore[arg-type]
            is None
        )
