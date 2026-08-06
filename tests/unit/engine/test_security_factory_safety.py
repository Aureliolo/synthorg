"""Tests for safety classifier and uncertainty checker wiring in the factory."""

from unittest.mock import MagicMock

import pytest

from synthorg.config.schema import ProviderConfig
from synthorg.engine._security_factory import SecurityLlmInfra
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.security.config import (
    SafetyClassifierConfig,
    SecurityConfig,
    UncertaintyCheckConfig,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of


def _llm_infra(*, model_resolver: ModelResolver | None = None) -> SecurityLlmInfra:
    """Build the LLM bundle over mock provider infrastructure.

    Args:
        model_resolver: Resolver for the uncertainty check; ``None`` models
            a deployment where the multi-provider fan-out is unavailable.

    Returns:
        A bundle carrying two registered connections.
    """
    registry = mock_of[ProviderRegistry](
        list_providers=MagicMock(return_value=("p-a", "p-b")),
    )
    configs: dict[str, ProviderConfig] = {
        "p-a": ProviderConfig(family="family-a", connection_name="conn-a"),
        "p-b": ProviderConfig(family="family-b", connection_name="conn-b"),
    }
    return SecurityLlmInfra(
        provider_registry=registry,
        config_resolver=mock_of[ConfigResolverProtocol](),
        provider_configs=configs,
        model_resolver=model_resolver,
    )


@pytest.mark.unit
class TestFactorySafetyClassifierWiring:
    """Factory wires SafetyClassifier when config enabled + providers."""

    def test_wired_when_enabled_and_providers_available(self) -> None:
        from synthorg.engine._security_factory import (
            make_security_interceptor,
        )
        from synthorg.security.audit import AuditLog

        cfg = SecurityConfig(
            safety_classifier=SafetyClassifierConfig(enabled=True),
        )

        svc = make_security_interceptor(cfg, AuditLog(), llm_infra=_llm_infra())

        assert svc is not None
        assert svc._safety_classifier is not None  # type: ignore[attr-defined]

    def test_not_wired_when_disabled(self) -> None:
        from synthorg.engine._security_factory import (
            make_security_interceptor,
        )
        from synthorg.security.audit import AuditLog

        cfg = SecurityConfig(
            safety_classifier=SafetyClassifierConfig(enabled=False),
        )

        svc = make_security_interceptor(cfg, AuditLog(), llm_infra=_llm_infra())

        assert svc is not None
        assert svc._safety_classifier is None  # type: ignore[attr-defined]

    def test_not_wired_when_no_providers(self) -> None:
        from synthorg.engine._security_factory import (
            make_security_interceptor,
        )
        from synthorg.security.audit import AuditLog

        cfg = SecurityConfig(
            safety_classifier=SafetyClassifierConfig(enabled=True),
        )

        svc = make_security_interceptor(cfg, AuditLog())

        assert svc is not None
        assert svc._safety_classifier is None  # type: ignore[attr-defined]


@pytest.mark.unit
class TestFactoryUncertaintyCheckerWiring:
    """Factory wires UncertaintyChecker when config + providers + resolver."""

    def test_wired_when_enabled_and_all_deps(self) -> None:
        from synthorg.engine._security_factory import (
            make_security_interceptor,
        )
        from synthorg.security.audit import AuditLog

        cfg = SecurityConfig(
            uncertainty_check=UncertaintyCheckConfig(
                enabled=True,
                model_ref="small",
            ),
        )

        svc = make_security_interceptor(
            cfg,
            AuditLog(),
            llm_infra=_llm_infra(model_resolver=mock_of[ModelResolver]()),
        )

        assert svc is not None
        assert svc._uncertainty_checker is not None  # type: ignore[attr-defined]

    def test_not_wired_when_no_resolver(self) -> None:
        from synthorg.engine._security_factory import (
            make_security_interceptor,
        )
        from synthorg.security.audit import AuditLog

        cfg = SecurityConfig(
            uncertainty_check=UncertaintyCheckConfig(
                enabled=True,
                model_ref="small",
            ),
        )

        svc = make_security_interceptor(cfg, AuditLog(), llm_infra=_llm_infra())

        assert svc is not None
        assert svc._uncertainty_checker is None  # type: ignore[attr-defined]
