# module-kind: code
"""Process-singleton cache for the resolved fine-tune image reference.

Decouples the env-var read (canonically owned by ``ConfigResolver``
via the registered ``memory.fine_tune_image`` setting, seeded by the
CLI through ``SYNTHORG_FINE_TUNE_IMAGE``) from the run-start default
resolution in :mod:`synthorg.memory.embedding.fine_tune_run_helpers`.

Unlike the sandbox image cache there is deliberately no fallback
constant: an empty value is a meaningful state ("no image configured"),
which derives the in-process execution backend for bare-metal installs.

Lifecycle wiring (``synthorg.api.lifecycle_helpers.config_apply``)
resolves the setting once per startup and calls the setter here. Test
fixtures must restore the cache via ``set_resolved_fine_tune_image(None)``
on teardown to avoid leaking a value across tests.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_RESOLVER_CACHE_RESOLVED

logger = get_logger(__name__)

_resolved_fine_tune_image: str | None = None


def set_resolved_fine_tune_image(value: str | None) -> None:
    """Set the resolved fine-tune image; ``None`` or blank clears the cache.

    Called once at startup after resolving ``memory.fine_tune_image``
    through ``ConfigResolver``. Whitespace-only inputs are normalised
    to ``None`` so the getter reports "not configured" instead of an
    invalid image reference.
    """
    global _resolved_fine_tune_image  # noqa: PLW0603 -- module-level cache
    normalized = value.strip() if value is not None else None
    _resolved_fine_tune_image = normalized or None


def get_resolved_fine_tune_image() -> str:
    """Return the cached fine-tune image, or ``""`` when not configured.

    An empty result means no fine-tune image is configured on this
    install; execution-config resolution derives the in-process backend
    from it.

    Returns:
        Result of type ``str``.
    """
    if _resolved_fine_tune_image:
        logger.debug(
            CONFIG_RESOLVER_CACHE_RESOLVED,
            var="memory.fine_tune_image",
            source="resolver_cache",
            resolved=_resolved_fine_tune_image,
        )
        return _resolved_fine_tune_image
    return ""
