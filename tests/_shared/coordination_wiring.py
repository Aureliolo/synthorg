"""Test helper: bind an explicit coordination decomposition model.

The coordinator builds eagerly at boot and its fallback decomposer + routing
judge dispatch on ``coordination.decomposition_model``, which is never
auto-resolved to a default provider. A harness that registers a provider and
expects an online coordinator must therefore set an explicit ``(provider,
model)`` reference, exactly as a configured deployment would.
"""

from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.service import SettingsService


async def wire_decomposition_model(
    settings_service: SettingsService,
    *,
    provider: str = "test-provider",
    model_id: str = "example-medium-001",
) -> None:
    """Set ``coordination.decomposition_model`` to a bound ``(provider, model)``.

    Args:
        settings_service: The live settings service backing the runtime.
        provider: The registered provider name the decomposition model binds to.
        model_id: The model id (opaque to a scripted driver).
    """
    await settings_service.set(
        "coordination",
        "decomposition_model",
        serialize_model_ref(ModelRef(provider=provider, model_id=model_id)),
    )
