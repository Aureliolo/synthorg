"""Ask-policy settings subscriber.

Re-binds the ambient ask-policy provider when an operator toggles the standing
directive or edits the operator-authored additions, so the change lands on the
next prompt build with no restart.

It deliberately does NOT watch ``engine.clarification_enabled`` /
``engine.scoping_enabled``. Those change the agent toolset, which only a runtime
rebuild can install, so they stay owned by ``RuntimeReloadSettingsSubscriber``.
The directive text names no tool, so nothing here depends on them.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SUBSCRIBER_NOTIFIED
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("engine", "ask_policy_enabled"),
        ("engine", "ask_policy_extra_directives"),
    }
)


class AskPolicySettingsSubscriber:
    """Re-bind the ambient ask-policy provider on a watched edit.

    Args:
        app_state: Held for symmetry with peer subscribers (unused: the ambient
            provider is process-global, not an app-state slice).
        settings_service: The live resolver the rebuild reads the new config from.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "ask-policy"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-bind the ask-policy provider once for the whole batch.

        The rebuild re-reads every watched key whichever one changed, so a
        batch carrying several of them would otherwise re-bind the same
        provider once per key for one identical result.

        Args:
            changes: The watched writes to apply.
        """
        applies = False
        for namespace, key in changes:
            if (namespace, key) in _WATCHED:
                applies = True
                continue
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
        if applies:
            await self._apply()

    async def _apply(self) -> None:
        """Re-bind the ask-policy provider so the new values go live."""
        from synthorg.engine.ask_policy.wiring import (  # noqa: PLC0415
            rebuild_and_bind_ask_policy,
        )

        # No try/except: the rebuild reports a settings fault through its own
        # logging and keeps the last known-good binding rather than raising,
        # so a handler here would be unreachable for the failure it names.
        # A critical error is meant to propagate and abort the dispatch.
        # It emits ASK_POLICY_PROVIDER_REBOUND (or _RETAINED) itself, so there
        # is no second emission here.
        await rebuild_and_bind_ask_policy(self._settings_service)
