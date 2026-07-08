"""Unit tests for ``resolve_feature_provider`` model-ref resolution."""

import pytest

from synthorg.api._feature_provider_resolution import resolve_feature_provider
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _registry() -> tuple[
    ProviderRegistry, BaseCompletionProvider, BaseCompletionProvider
]:
    """Two providers: ``cloud`` serves ``glm-5.2``; ``local`` serves nothing."""
    cloud = mock_of[BaseCompletionProvider](serves_model=lambda m: m == "glm-5.2")
    local = mock_of[BaseCompletionProvider](serves_model=lambda _m: False)
    return ProviderRegistry({"ollama-cloud": cloud, "ollama": local}), cloud, local


def test_explicit_provider_ref_binds_to_that_driver() -> None:
    """An explicit provider in the ref binds to that driver, not resolve-by-model."""
    registry, cloud, local = _registry()
    ref = '{"provider": "ollama", "model_id": "glm-5.2"}'
    # ``ollama`` does not serve glm-5.2, but the operator pinned it explicitly:
    # honour the pin rather than routing to whichever driver serves the model.
    assert resolve_feature_provider(registry, ref, feature="charter") is local
    assert local is not cloud


def test_bare_string_resolves_by_model() -> None:
    """A provider-less (legacy bare) value keeps resolve-by-model behaviour."""
    registry, cloud, _local = _registry()
    assert resolve_feature_provider(registry, "glm-5.2", feature="charter") is cloud


def test_explicit_provider_absent_stays_unwired() -> None:
    """An explicit-but-unregistered provider degrades to ``None`` (unwired)."""
    registry, _cloud, _local = _registry()
    ref = '{"provider": "absent-provider", "model_id": "glm-5.2"}'
    assert resolve_feature_provider(registry, ref, feature="charter") is None


def test_provider_only_ref_stays_unwired() -> None:
    """A ref with a provider but no model id is unconfigured -> ``None``."""
    registry, _cloud, _local = _registry()
    ref = '{"provider": "ollama-cloud", "model_id": ""}'
    assert resolve_feature_provider(registry, ref, feature="charter") is None


def test_empty_value_stays_unwired() -> None:
    registry, _cloud, _local = _registry()
    assert resolve_feature_provider(registry, "", feature="charter") is None
    assert resolve_feature_provider(registry, None, feature="charter") is None
