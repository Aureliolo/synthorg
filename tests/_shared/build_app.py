"""Test helper that builds a Litestar app from the legacy keyword arguments.

``create_app`` collapsed its ~27 dependency-injection keyword arguments into a
single :class:`~synthorg.api.app_overrides.AppOverrides` bundle. Tests that
inject doubles keep passing the individual kwargs through this shim, which
bundles them into ``AppOverrides`` and forwards ``config`` /
``_skip_lifecycle_shutdown`` unchanged. Call sites are a mechanical
``create_app(`` -> ``build_test_app(`` rename with identical kwargs.
"""

from typing import Any

from litestar import Litestar

from synthorg.api.app import create_app
from synthorg.api.app_overrides import AppOverrides
from synthorg.config.schema import RootConfig


def build_test_app(
    *,
    config: RootConfig | None = None,
    _skip_lifecycle_shutdown: bool = False,
    **injections: Any,
) -> Litestar:
    """Build a Litestar app, bundling injection kwargs into ``AppOverrides``.

    Args:
        config: Root company configuration (forwarded unchanged).
        _skip_lifecycle_shutdown: Forwarded unchanged.
        **injections: Any ``AppOverrides`` field (``persistence``,
            ``message_bus``, ``cost_tracker``, etc.) to inject.

    Returns:
        The configured Litestar application.
    """
    return create_app(
        config=config,
        overrides=AppOverrides(**injections),
        _skip_lifecycle_shutdown=_skip_lifecycle_shutdown,
    )
