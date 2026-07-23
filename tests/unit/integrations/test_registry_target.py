"""Unit tests for the registry-target metadata vocabulary.

The resolvers are security-relevant defaults rather than conveniences: the
channel decides the approval action type, and the provider decides whether a
target is usable at all. Each therefore fails toward the stricter (or
setup-forcing) answer, and says so in the log when the operator's value was
present but unreadable.
"""

import pytest

from synthorg.integrations.connections.registry_target import (
    PublishMethod,
    RegistryChannel,
    RegistryProvider,
    resolve_channel,
    resolve_default_method,
    resolve_provider,
    resolve_repository,
)

pytestmark = pytest.mark.unit


class TestResolveChannel:
    def test_declared_channel_is_honoured(self) -> None:
        assert resolve_channel({"channel": "staging"}) is RegistryChannel.STAGING

    @pytest.mark.parametrize(
        "metadata",
        [{}, {"channel": ""}, {"channel": "Production"}, {"channel": "x"}],
        ids=["absent", "blank", "wrong-case", "unknown"],
    )
    def test_unreadable_channel_resolves_to_production(
        self, metadata: dict[str, str]
    ) -> None:
        """A mislabelled target is over-gated, never treated as throwaway."""
        assert resolve_channel(metadata) is RegistryChannel.PRODUCTION


class TestResolveProvider:
    def test_declared_provider_is_honoured(self) -> None:
        assert (
            resolve_provider({"provider": "generic_oci"})
            is RegistryProvider.GENERIC_OCI
        )

    @pytest.mark.parametrize(
        "metadata",
        [{}, {"provider": ""}, {"provider": "not-a-provider"}],
        ids=["absent", "blank", "unknown"],
    )
    def test_unreadable_provider_resolves_to_none(
        self, metadata: dict[str, str]
    ) -> None:
        """``None`` is a setup problem for a human, never a guessed default."""
        assert resolve_provider(metadata) is None


class TestResolveRepository:
    def test_declared_repository_is_stripped(self) -> None:
        assert resolve_repository({"repository": "  org/app  "}) == "org/app"

    @pytest.mark.parametrize("metadata", [{}, {"repository": ""}, {"repository": " "}])
    def test_absent_repository_is_empty(self, metadata: dict[str, str]) -> None:
        assert resolve_repository(metadata) == ""


class TestResolveDefaultMethod:
    def test_declared_method_is_honoured(self) -> None:
        assert (
            resolve_default_method({"default_publish_method": "workspace_push"})
            is PublishMethod.WORKSPACE_PUSH
        )

    @pytest.mark.parametrize(
        "metadata",
        [{}, {"default_publish_method": ""}, {"default_publish_method": "x"}],
        ids=["absent", "blank", "unknown"],
    )
    def test_unreadable_method_resolves_to_auto(self, metadata: dict[str, str]) -> None:
        """An unset default degrades to input-driven selection, not a fixed method."""
        assert resolve_default_method(metadata) is PublishMethod.AUTO
