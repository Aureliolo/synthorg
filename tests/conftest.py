"""Root test configuration and shared fixtures."""

import logging
import os

import structlog
from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("dev", max_examples=1000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def clear_logging_state() -> None:
    """Clear structlog context and stdlib root handlers.

    Shared helper for observability test fixtures that need to reset
    logging state between tests.
    """
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


def _patched_configure(
    *args: object,
    _original: object = structlog.configure,
    **kwargs: object,
) -> None:
    """Force ``cache_logger_on_first_use=False`` during tests.

    ``configure_logging()`` sets ``cache_logger_on_first_use=True`` which
    causes module-level structlog proxies to permanently cache their bound
    loggers.  After ``structlog.reset_defaults()`` creates a *new* default
    processor list, cached proxies still reference the *old* list, so
    ``structlog.testing.capture_logs()`` — which mutates the current list
    in-place — can no longer intercept events from those proxies.

    By forcing ``cache_logger_on_first_use=False`` globally during tests,
    proxies resolve fresh on every call, always reading the current
    processor list.
    """
    kwargs["cache_logger_on_first_use"] = False
    _original(*args, **kwargs)  # type: ignore[operator]


structlog.configure = _patched_configure
