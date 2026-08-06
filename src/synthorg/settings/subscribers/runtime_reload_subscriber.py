"""Runtime-services reload settings subscriber.

Triggers ``workers.runtime_builder.reload_runtime_services`` when an operator
edits a setting that ``build_runtime_services`` already re-reads from the live
resolver but only consults on a rebuild: the engine quality-classifier /
model-matcher knobs, the external-API runtime gate, and the coordination
middleware toggle. The rebuild hot-swaps the agent engine, coordinator, work
pipeline, and entry adapters with no process restart.

A single subscriber owns these keys because they all converge on the same
``reload_runtime_services`` call, and writes arrive in bursts: an operator
saving a settings form, or first-run setup writing a form's worth of model
refs, produces one notification per field. Serving each with its own rebuild
tore down and rebuilt the engine, coordinator and pipeline once per key while
the org was trying to answer. Writes are therefore batched over a quiet window
and served by one rebuild, without weakening what a write promises: it still
returns only after a rebuild that started AFTER it, and still sees that
rebuild's failure.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_RUNTIME_RELOAD_COALESCED,
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

# Used when the window's own setting cannot be read, which is the boot window
# before the resolver is wired. Matches the registered default rather than
# standing in for it: a burst during boot is exactly the case (first-run setup
# writes one) and falling back to no window would leave it uncoalesced.
_DEFAULT_COALESCE_WINDOW_SECONDS: Final[float] = 0.75

# How many keys a batch names in its rebuild trigger before it stops listing
# them. The trigger is a log label, and a burst carries every key an operator
# touched; naming two and counting the rest keeps it readable without
# pretending the batch was smaller than it was.
_TRIGGER_NAMED_KEYS: Final[int] = 2

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("engine", "classifier_rule_matched_confidence"),
        ("engine", "classifier_fallback_confidence"),
        ("engine", "classification_detector_timeout_seconds"),
        ("engine", "matcher_min_usable_parameters"),
        ("engine", "matcher_prefer_local"),
        ("engine", "matcher_min_cloud_tier"),
        ("external_api", "enabled"),
        ("external_api", "provider_type"),
        ("coordination", "enable_coordination_middleware"),
        ("engine", "enable_agent_middleware"),
        # The coordinator builds eagerly and hard-requires a non-blank
        # decomposition model, so setting it must rebuild the coordinator
        # live: without this, first-run setup writes the model AFTER a
        # capability toggle already tried (and failed) to build the
        # coordinator, leaving it broken until a manual restart.
        ("coordination", "decomposition_model"),
        # The planning session's memory grant + digest are resolved by
        # ``build_planning_memory`` when ``build_runtime_services`` (re)builds
        # the coordinator; watch them so a toggle / budget change goes live on
        # the next rebuild rather than waiting for an unrelated one or a restart.
        ("memory", "planning_memory_recall_enabled"),
        ("memory", "planning_memory_digest_budget"),
        # The procedural proposer bakes its sampling parameters and skill-file
        # directory in at construction, inside the boot engine.
        ("memory", "procedural_temperature"),
        ("memory", "procedural_max_tokens"),
        ("memory", "procedural_skill_md_directory"),
        ("design", "image_generation_enabled"),
        ("design", "image_model"),
        # The tool registry and the planning-agent grant bake in the native
        # web-search provider, which ``build_runtime_services`` re-resolves from
        # these keys on a rebuild; without watching them a provider / connection
        # / ceiling change would not go live until an unrelated rebuild.
        ("tools", "web_search_enabled"),
        ("tools", "web_search_provider"),
        ("tools", "web_search_connection"),
        ("tools", "web_search_max_results"),
        # The tool registry resolves the desktop session's driver and screen
        # geometry into the DesktopTool it builds, so an edit reaches a
        # session only through a rebuild.
        ("tools", "desktop_driver"),
        ("tools", "desktop_screen_width"),
        ("tools", "desktop_screen_height"),
        ("tools", "desktop_image_pin"),
        ("tools", "browser_image_pin"),
        # The auto-loop's container image and its two lifetime budgets are
        # resolved once, when the loop dependencies are built, and then held
        # on the engine for its lifetime. Without a rebuild the operator's
        # edit reaches no run at all.
        ("tools", "openhands_image"),
        ("tools", "openhands_idle_timeout_seconds"),
        ("tools", "openhands_max_runtime_seconds"),
        # The capability master and the two endpoints decide whether
        # ``build_openhands_loop_deps_or_none`` returns deps at all, and the
        # endpoints are additionally baked into the sandbox egress allowlist.
        # All three are read inside the rebuild, so without them here an
        # operator wiring the loop sees no change until an unrelated rebuild.
        ("tools", "openhands_enabled"),
        ("tools", "credentialed_mcp_base_url"),
        ("providers", "gateway_base_url"),
        # Per-task loop selection is resolved into the frozen AutoLoopConfig
        # the engine holds for its lifetime, and the per-task resolution reads
        # that snapshot rather than the resolver, so an edit reaches no task
        # until the engine is rebuilt.
        ("engine", "loop_auto_select_enabled"),
        ("engine", "default_loop_type"),
        ("engine", "loop_complexity_overrides"),
        # The boot engine captures the memory backend by value, so replacing
        # the backend (which the memory_backend subsystem does on any of
        # these) leaves the engine reading and writing through the instance
        # that was just disconnected. Rebuilding it here is what makes the
        # replacement reach an agent rather than only the slice.
        ("memory", "backend"),
        ("memory", "embedder_model"),
        ("memory", "embedder_dims"),
        ("memory", "consolidation_interval"),
        # Both are resolved into the boot AgentEngine when
        # ``build_runtime_services`` (re)builds it, so an edit reaches a run
        # only through a rebuild.
        ("engine", "clarification_enabled"),
        ("engine", "scoping_enabled"),
        # Auto-review + the completion oracle are wired into the runtime on a
        # rebuild: the pipeline is (re)built by ``build_runtime_services`` and
        # the oracle gates are re-attached to the review-gate service, so an
        # edit to any of these applies on the next task without a restart.
        ("engine", "auto_review_on_completion"),
        ("engine", "completion_oracle_enabled"),
        ("engine", "completion_oracle_shadow_mode"),
        ("engine", "completion_oracle_min_stakes"),
        ("engine", "completion_oracle_reviewer_model"),
        # Each names the connection one runtime collaborator dispatches on,
        # and each is resolved into that collaborator while
        # ``build_runtime_services`` assembles it. Reassigning the model is
        # exactly the edit an operator expects to take effect on the next
        # run, so the rebuild is what makes the choice reach anything.
        ("engine", "evolution_proposer_model"),
        ("security", "red_team_model"),
        ("security", "vision_verify_model"),
    }
)


@dataclass(slots=True)
class _Batch:
    """Writes waiting on one shared rebuild.

    Attributes:
        done: Resolved with the rebuild's outcome. Every writer that joined
            awaits this same future, so all of them see one rebuild's success
            or its failure.
        pairs: The ``(namespace, key)`` writes this rebuild will carry, kept
            for the trigger label an operator reads in the logs.
        runner: Strong reference to the task driving the batch. A task with no
            live reference can be collected mid-flight, which would drop the
            rebuild and leave every joiner waiting on a future nothing will
            ever resolve.
    """

    done: asyncio.Future[None]
    pairs: set[tuple[str, str]] = field(default_factory=set)
    runner: asyncio.Task[None] | None = None


def _trigger(pairs: set[tuple[str, str]]) -> str:
    """Render a rebuild trigger naming the writes that caused it.

    Args:
        pairs: The writes the batch carried.

    Returns:
        A label naming up to :data:`_TRIGGER_NAMED_KEYS` of them, plus a count
        of the rest.
    """
    keys = sorted(f"{namespace}.{key}" for namespace, key in pairs)
    named = ",".join(keys[:_TRIGGER_NAMED_KEYS])
    rest = len(keys) - _TRIGGER_NAMED_KEYS
    return f"setting:{named}" if rest <= 0 else f"setting:{named}+{rest}"


class RuntimeReloadSettingsSubscriber:
    """Rebuild runtime services on a watched engine/external_api/coordination edit.

    Args:
        app_state: Application state passed to ``reload_runtime_services``.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service
        # The batch still open for joiners, or None when the next write starts
        # a fresh one. Guarded by a threading lock rather than an asyncio one
        # because every operation on it is synchronous, and because this
        # subscriber is held on an AppState that can outlive a single loop: a
        # per-loop lock would silently stop serialising across two.
        self._pending: _Batch | None = None
        self._guard = threading.Lock()

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "runtime-reload"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Trigger a runtime-services rebuild so the new value goes live.

        Returns once a rebuild that began after this write has finished, and
        raises whatever that rebuild raised. The rebuild may be shared with
        other writes that landed inside the same window; sharing one is not
        the same as skipping one, so nothing this call promises is weakened.

        Raises:
            Exception: Whatever the rebuild raised, re-raised to every writer
                the batch served so a failure reaches the operator who caused
                it rather than only the one who happened to open the batch.
        """
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        batch = self._join(namespace, key)
        # Shielded: a cancelled writer must not cancel the shared rebuild out
        # from under every other writer waiting on it, and the runtime would
        # be left half-swapped for all of them.
        await asyncio.shield(batch.done)

    def _join(self, namespace: str, key: str) -> _Batch:
        """Add this write to the batch that will carry it.

        Args:
            namespace: The written setting's namespace.
            key: The written setting's key.

        Returns:
            The batch whose rebuild starts after this write.
        """
        with self._guard:
            batch = self._pending
            if batch is None:
                batch = _Batch(done=asyncio.get_running_loop().create_future())
                self._pending = batch
                batch.runner = asyncio.create_task(self._run(batch))
            batch.pairs.add((namespace, key))
            return batch

    async def _run(self, batch: _Batch) -> None:
        """Wait out the window, then rebuild once for everything that joined.

        Args:
            batch: The batch this rebuild serves.

        Raises:
            CancelledError: When the batch is cancelled mid-rebuild, after
                handing every joiner the same cancellation.
        """
        await asyncio.sleep(await self._window_seconds())
        with self._guard:
            # Closed before the rebuild runs, not after: a write arriving from
            # here on needs a rebuild that starts after IT, so it opens the
            # next batch rather than joining one already under way.
            if self._pending is batch:
                self._pending = None
            pairs = set(batch.pairs)
        try:
            await self._reload(pairs)
        except asyncio.CancelledError:
            # Shutdown, not a rebuild failure. Cancelling the future hands
            # every joiner that verdict rather than leaving them waiting on a
            # task that has gone.
            batch.done.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 -- delivered to every joiner
            # Settled BEFORE anything re-raises: a joiner waiting on a future
            # that a propagating error left unset waits forever. ``_reload``
            # has already logged this, so the delivery below is where it
            # reaches an operator, not a swallow.
            self._settle(batch, exc)
            reraise_critical(exc)
            return
        self._settle(batch, None)

    @staticmethod
    def _settle(batch: _Batch, exc: Exception | None) -> None:
        """Hand the rebuild's outcome to everything waiting on it.

        Args:
            batch: The batch to resolve.
            exc: The rebuild's failure, or ``None`` when it succeeded.
        """
        if batch.done.done():
            return
        if exc is None:
            batch.done.set_result(None)
        else:
            batch.done.set_exception(exc)

    async def _reload(self, pairs: set[tuple[str, str]]) -> None:
        """Rebuild the runtime services for one batch of writes.

        Args:
            pairs: The writes this rebuild carries.

        Raises:
            Exception: Whatever the rebuild raised, after logging it.
        """
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        if len(pairs) > 1:
            logger.info(
                SETTINGS_RUNTIME_RELOAD_COALESCED,
                subscriber=self.subscriber_name,
                writes=len(pairs),
            )
        try:
            await reload_runtime_services(self._app_state, trigger=_trigger(pairs))
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="runtime_services",
                trigger=_trigger(pairs),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _window_seconds(self) -> float:
        """Resolve how long to wait for further writes before rebuilding.

        Read per batch rather than held, so an operator widening the window
        after a burst of rebuilds sees the next burst honour it.

        Returns:
            The configured window, or the shipped default when the resolver is
            not wired yet.
        """
        resolver = self._app_state.slice(SettingsStateSlice).config_resolver
        if resolver is None:
            return _DEFAULT_COALESCE_WINDOW_SECONDS
        return await resolver.get_float(
            "engine", "runtime_reload_coalesce_window_seconds"
        )
