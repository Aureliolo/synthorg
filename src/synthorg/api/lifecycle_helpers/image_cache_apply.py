# module-kind: code
"""Populate the boot image-resolution caches from settings.

Sandbox / sidecar and fine-tune images resolve once at startup through
the canonical DB > env > default chain; these appliers seed the
process-level caches so field defaults and per-run resolvers stop
reading ``os.environ`` directly.
"""

import asyncio

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def _apply_sandbox_image_cache(app_state: AppState) -> None:
    """Populate the sandbox / sidecar image-resolution cache from settings.

    Called once per startup so ``DockerSandboxConfig`` field defaults
    stop reading ``os.environ`` directly. ``env_var_override`` on the
    registered settings preserves the historical
    ``SYNTHORG_SANDBOX_IMAGE`` / ``SYNTHORG_SIDECAR_IMAGE`` workflow
    without bypassing the canonical DB > env > YAML > default chain.

    Resolver failures clear the cache to ``None`` so the field default
    falls through to the documented constant; whitespace-only resolver
    results are normalised to ``None`` in the caller (the setter also
    normalises, but stripping here makes the intent explicit).

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.tools.sandbox._image_resolution import (  # noqa: PLC0415
        set_resolved_sandbox_image,
        set_resolved_sidecar_image,
    )

    for setting_key, setter in (
        ("sandbox_image", set_resolved_sandbox_image),
        ("sidecar_image", set_resolved_sidecar_image),
    ):
        try:
            image_value = await config_resolver_of(app_state).get_str(
                SettingNamespace.TOOLS.value, setting_key
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            setter(None)
            logger.warning(
                API_APP_STARTUP,
                setting=f"tools.{setting_key}",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            stripped = image_value.strip() if image_value is not None else None
            setter(stripped or None)


async def _apply_fine_tune_image_cache(app_state: AppState) -> None:
    """Populate the fine-tune image-resolution cache from settings.

    Same shape as :func:`_apply_sandbox_image_cache`; the cache has no
    fallback constant because an empty value is meaningful (no image
    configured derives the in-process execution backend).

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.memory.embedding.fine_tune_image_resolution import (  # noqa: PLC0415
        set_resolved_fine_tune_image,
    )

    try:
        image_value = await config_resolver_of(app_state).get_str(
            SettingNamespace.MEMORY.value, "fine_tune_image"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        set_resolved_fine_tune_image(None)
        logger.warning(
            API_APP_STARTUP,
            setting="memory.fine_tune_image",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    else:
        set_resolved_fine_tune_image(image_value)
