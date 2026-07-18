"""Unit tests for the shared MODEL_REF provider binding helper."""

import pytest

from synthorg.api.state import AppState
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.model_binding import resolve_ref_provider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_EVENT = "api.app.startup"


def _app_state() -> tuple[AppState, BaseCompletionProvider, BaseCompletionProvider]:
    """App state with ``cloud`` + ``local`` providers registered."""
    cloud = mock_of[BaseCompletionProvider]()
    local = mock_of[BaseCompletionProvider]()
    registry = ProviderRegistry({"ollama-cloud": cloud, "ollama": local})
    return make_app_state(provider_registry=registry), cloud, local


def test_explicit_provider_binds_to_that_driver() -> None:
    app_state, _cloud, local = _app_state()
    ref = ModelRef(provider="ollama", model_id="glm-5.2")
    resolved = resolve_ref_provider(
        app_state, ref, event=_EVENT, subject="decomposition"
    )
    assert resolved is local


def test_empty_provider_returns_none() -> None:
    # A provider-less ref is never auto-resolved to a default: a model
    # assignment must name its provider explicitly.
    app_state, _cloud, _local = _app_state()
    ref = ModelRef(provider="", model_id="glm-5.2")
    resolved = resolve_ref_provider(
        app_state, ref, event=_EVENT, subject="decomposition"
    )
    assert resolved is None


def test_unregistered_provider_returns_none() -> None:
    app_state, _cloud, _local = _app_state()
    ref = ModelRef(provider="absent-provider", model_id="glm-5.2")
    resolved = resolve_ref_provider(
        app_state, ref, event=_EVENT, subject="decomposition"
    )
    assert resolved is None
