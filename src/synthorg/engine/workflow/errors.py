"""Workflow-engine lifecycle error types.

Kept separate from ``blueprint_errors.py`` (blueprint lookup /
validation) so the webhook-bridge lifecycle conflict has a home that
does not pull blueprint concerns into the integrations import graph.
"""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class WebhookBridgeUnrestartableError(ConflictError):
    """Raised when ``WebhookEventBridge.start()`` is called after a timed-out stop.

    A stuck drain leaves the bridge's poll loop alive on the original
    instance, so the canonical lifecycle pattern marks the bridge
    unrestartable rather than stacking a second loop on the orphan.
    Mirrors :class:`~synthorg.providers.errors.ProviderLifecycleConflictError`;
    inherits the shareable ``RESOURCE_CONFLICT`` code.
    """

    default_message: ClassVar[str] = (
        "WebhookEventBridge is unrestartable after a timed-out stop"
    )
