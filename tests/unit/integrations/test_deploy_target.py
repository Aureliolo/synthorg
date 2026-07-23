"""Unit tests for the deploy-target metadata vocabulary.

Both resolvers are security-relevant defaults rather than conveniences:
the environment decides the approval action type, and the platform
decides whether a target is usable at all. Each therefore fails toward
the stricter answer, and says so in the log when the operator's value was
present but unreadable.
"""

import pytest

from synthorg.integrations.connections.deploy_target import (
    DeployEnvironment,
    DeployPlatform,
    resolve_environment,
    resolve_platform,
)

pytestmark = pytest.mark.unit


class TestResolveEnvironment:
    def test_declared_environment_is_honoured(self) -> None:
        assert (
            resolve_environment({"environment": "staging"}) is DeployEnvironment.STAGING
        )

    @pytest.mark.parametrize(
        "metadata",
        [{}, {"environment": ""}, {"environment": "Production"}, {"environment": "x"}],
        ids=["absent", "blank", "wrong-case", "unknown"],
    )
    def test_unreadable_environment_resolves_to_production(
        self, metadata: dict[str, str]
    ) -> None:
        """A mislabelled target is over-gated, never treated as throwaway."""
        assert resolve_environment(metadata) is DeployEnvironment.PRODUCTION


class TestResolvePlatform:
    def test_declared_platform_is_honoured(self) -> None:
        assert resolve_platform({"platform": "vercel"}) is DeployPlatform.VERCEL

    @pytest.mark.parametrize(
        "metadata",
        [{}, {"platform": ""}, {"platform": "not-a-platform"}],
        ids=["absent", "blank", "unknown"],
    )
    def test_unreadable_platform_resolves_to_none(
        self, metadata: dict[str, str]
    ) -> None:
        """``None`` is a setup problem for a human, never a guessed default."""
        assert resolve_platform(metadata) is None
