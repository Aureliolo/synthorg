"""Process-singleton cache for resolved sandbox / sidecar image references.

Decouples the env-var read (canonically owned by ``ConfigResolver``
via the registered ``tools.sandbox_image`` / ``tools.sidecar_image``
settings) from the Pydantic field default in
:mod:`synthorg.tools.sandbox.docker_config`. Without this seam,
constructing a ``DockerSandboxConfig`` instance any time after process
start would re-read ``SYNTHORG_SANDBOX_IMAGE`` directly from
``os.environ``, bypassing the canonical DB > env > YAML > default
resolution chain.

Lifecycle wiring (``synthorg.api.lifecycle_helpers._apply_bridge_config``)
resolves the two settings once per startup and calls the setters here.
The ``_default_*_image`` factories in
:mod:`synthorg.tools.sandbox.docker_config` then read from this cache,
falling back to the documented constant when the cache is unset
(programmatic instantiation outside lifecycle / test fixtures).

The cache is single-thread asyncio-safe by construction (Python's GIL
makes the read/write atomic for module-level strings); test fixtures
must restore the cache via ``set_resolved_*_image(None)`` on teardown
to avoid leaking a value across tests.
"""

from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.config import (
    CONFIG_ENV_VAR_FALLBACK,
    CONFIG_ENV_VAR_RESOLVED,
)

logger = get_logger(__name__)

_FALLBACK_SANDBOX_IMAGE: Final[str] = "ghcr.io/aureliolo/synthorg-sandbox:latest"
_FALLBACK_SIDECAR_IMAGE: Final[str] = "ghcr.io/aureliolo/synthorg-sidecar:latest"

_resolved_sandbox_image: str | None = None
_resolved_sidecar_image: str | None = None


def set_resolved_sandbox_image(value: str | None) -> None:
    """Set the resolved sandbox image; ``None`` clears the cache.

    Called once at startup by ``_apply_bridge_config`` after resolving
    ``tools.sandbox_image`` through ``ConfigResolver``. Tests use the
    same setter (with ``None`` on teardown) to override the cache.
    """
    global _resolved_sandbox_image  # noqa: PLW0603 -- module-level cache
    _resolved_sandbox_image = value


def set_resolved_sidecar_image(value: str | None) -> None:
    """Set the resolved sidecar image; ``None`` clears the cache."""
    global _resolved_sidecar_image  # noqa: PLW0603 -- module-level cache
    _resolved_sidecar_image = value


def get_resolved_sandbox_image() -> str:
    """Return the cached sandbox image, falling back to the constant.

    Logged at DEBUG so operators debugging image-resolution issues
    can see which source won (settings cache vs. fallback constant).
    """
    if _resolved_sandbox_image:
        logger.debug(
            CONFIG_ENV_VAR_RESOLVED,
            var="tools.sandbox_image",
            resolved=_resolved_sandbox_image,
        )
        return _resolved_sandbox_image
    logger.debug(
        CONFIG_ENV_VAR_FALLBACK,
        var="tools.sandbox_image",
        fallback=_FALLBACK_SANDBOX_IMAGE,
        reason="resolution_cache_unset",
    )
    return _FALLBACK_SANDBOX_IMAGE


def get_resolved_sidecar_image() -> str:
    """Return the cached sidecar image, falling back to the constant."""
    if _resolved_sidecar_image:
        logger.debug(
            CONFIG_ENV_VAR_RESOLVED,
            var="tools.sidecar_image",
            resolved=_resolved_sidecar_image,
        )
        return _resolved_sidecar_image
    logger.debug(
        CONFIG_ENV_VAR_FALLBACK,
        var="tools.sidecar_image",
        fallback=_FALLBACK_SIDECAR_IMAGE,
        reason="resolution_cache_unset",
    )
    return _FALLBACK_SIDECAR_IMAGE
