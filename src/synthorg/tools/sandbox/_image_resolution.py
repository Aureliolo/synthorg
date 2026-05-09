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
    CONFIG_FALLBACK_USED,
    CONFIG_RESOLVER_CACHE_RESOLVED,
)

logger = get_logger(__name__)

_FALLBACK_SANDBOX_IMAGE: Final[str] = "ghcr.io/aureliolo/synthorg-sandbox:latest"
_FALLBACK_SIDECAR_IMAGE: Final[str] = "ghcr.io/aureliolo/synthorg-sidecar:latest"

_resolved_sandbox_image: str | None = None
_resolved_sidecar_image: str | None = None


def set_resolved_sandbox_image(value: str | None) -> None:
    """Set the resolved sandbox image; ``None`` or blank clears the cache.

    Called once at startup by ``_apply_bridge_config`` after resolving
    ``tools.sandbox_image`` through ``ConfigResolver``. Tests use the
    same setter (with ``None`` on teardown) to override the cache.
    Whitespace-only inputs are normalised to ``None`` so the getter's
    fallback constant fires instead of returning an invalid image
    reference.
    """
    global _resolved_sandbox_image  # noqa: PLW0603 -- module-level cache
    normalized = value.strip() if value is not None else None
    _resolved_sandbox_image = normalized or None


def set_resolved_sidecar_image(value: str | None) -> None:
    """Set the resolved sidecar image; ``None`` or blank clears the cache."""
    global _resolved_sidecar_image  # noqa: PLW0603 -- module-level cache
    normalized = value.strip() if value is not None else None
    _resolved_sidecar_image = normalized or None


def get_resolved_sandbox_image() -> str:
    """Return the cached sandbox image, falling back to the constant.

    Logged at DEBUG with a ``source`` discriminator so operators can
    tell whether the value came from the resolver-populated cache (the
    canonical DB > env > YAML > default chain ran at startup) or from
    the documented fallback constant (cache never seeded -- typically
    a programmatic ``DockerSandboxConfig`` instantiation outside the
    lifecycle wiring).
    """
    if _resolved_sandbox_image:
        logger.debug(
            CONFIG_RESOLVER_CACHE_RESOLVED,
            var="tools.sandbox_image",
            source="resolver_cache",
            resolved=_resolved_sandbox_image,
        )
        return _resolved_sandbox_image
    logger.debug(
        CONFIG_FALLBACK_USED,
        var="tools.sandbox_image",
        source="fallback_constant",
        fallback=_FALLBACK_SANDBOX_IMAGE,
        reason="resolution_cache_unset",
    )
    return _FALLBACK_SANDBOX_IMAGE


def get_resolved_sidecar_image() -> str:
    """Return the cached sidecar image, falling back to the constant.

    Same source-discriminator semantics as
    :func:`get_resolved_sandbox_image`.
    """
    if _resolved_sidecar_image:
        logger.debug(
            CONFIG_RESOLVER_CACHE_RESOLVED,
            var="tools.sidecar_image",
            source="resolver_cache",
            resolved=_resolved_sidecar_image,
        )
        return _resolved_sidecar_image
    logger.debug(
        CONFIG_FALLBACK_USED,
        var="tools.sidecar_image",
        source="fallback_constant",
        fallback=_FALLBACK_SIDECAR_IMAGE,
        reason="resolution_cache_unset",
    )
    return _FALLBACK_SIDECAR_IMAGE
