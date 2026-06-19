"""Test helper that builds a Litestar app from individual injection kwargs.

``create_app`` takes a single
:class:`~synthorg.api.app_overrides.AppOverrides` bundle. Tests that inject
doubles pass the individual kwargs through this helper, which bundles them
into ``AppOverrides`` and forwards ``config`` / ``_skip_lifecycle_shutdown``
unchanged.
"""

from typing import Any

from litestar import Litestar

from synthorg.api.app import create_app
from synthorg.api.app_overrides import AppOverrides
from synthorg.config.schema import RootConfig


def build_test_app(  # type: ignore[explicit-any]  # forwards arbitrary AppOverrides fields into the typed bundle
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
