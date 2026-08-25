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
refs, produces one notification per field. One batch is therefore one rebuild.
The batching itself belongs to the dispatcher, which is the only place the
writes are ever simultaneously in hand; this subscriber simply treats the
batch it is handed as a single trigger.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_RUNTIME_RELOAD_COALESCED,
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingChange, describe_changes

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("engine", "classifier_rule_matched_confidence"),
        ("engine", "classifier_fallback_confidence"),
        ("engine", "classification_detector_timeout_seconds"),
        ("engine", "matcher_min_usable_parameters"),
        ("engine", "matcher_prefer_local"),
        ("engine", "matcher_min_cloud_cost_tier"),
        ("external_api", "enabled"),
        ("external_api", "provider_type"),
        # The agent -> SynthOrg-MCP bridge is composed once, when the engine
        # is built, and then held for its lifetime. Without a rebuild an
        # operator opening the bridge changes nothing an agent can reach, and
        # ``chief_of_staff.direct_mcp_enabled`` (whose gate reads
        # ``engine.has_mcp_self_consumer``) stays permanently blocked.
        ("security", "mcp_self_consumer_mode"),
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
        # The web_fetch ladder is assembled once per rebuild: which rungs
        # exist, and the budgets each one bakes in. Without watching these a
        # newly-enabled rung would be invisible until an unrelated rebuild.
        ("tools", "web_fetch_enabled"),
        ("tools", "web_fetch_proxy_enabled"),
        ("tools", "web_fetch_render_enabled"),
        ("tools", "web_fetch_max_characters"),
        ("tools", "web_fetch_max_response_bytes"),
        ("tools", "web_fetch_user_agent"),
        ("tools", "web_fetch_docs_index_discovery_enabled"),
        # Baked into every web tool and both provider ladders at wiring time,
        # by three separate readers. An operator raising it for a slow docs
        # host would otherwise wait for an unrelated rebuild to take effect.
        ("tools", "web_request_timeout_seconds"),
        # Each governed connection-tool family is composed once per rebuild,
        # into a boot-scoped bundle the per-run augmentation then binds an
        # identity onto. An operator repointing a family at a different
        # connection therefore reaches a running agent only here.
        ("tools", "forge_tools_enabled"),
        ("tools", "forge_tools_connection"),
        ("tools", "forge_tools_timeout_seconds"),
        ("tools", "forge_tools_max_read_chars"),
        ("tools", "chat_tools_enabled"),
        ("tools", "chat_tools_connection"),
        ("tools", "chat_tools_timeout_seconds"),
        # The deploy and publish tool bundles are composed once per rebuild:
        # which connection catalog backs them, which targets they may reach,
        # and the budgets each tool bakes in. Both families destroy or replace
        # upstream state, so an operator narrowing the allowed targets has to
        # reach a running agent without waiting for an unrelated rebuild.
        ("tools", "deploy_tools_enabled"),
        ("tools", "deploy_tools_targets"),
        ("tools", "deploy_tools_timeout_seconds"),
        ("tools", "deploy_tools_max_log_chars"),
        ("tools", "publish_tools_enabled"),
        ("tools", "publish_tools_targets"),
        ("tools", "publish_tools_timeout_seconds"),
        ("tools", "publish_tools_max_manifest_bytes"),
        ("tools", "publish_tools_max_image_bytes"),
        # The tool registry resolves the desktop session's driver and screen
        # geometry into the DesktopTool it builds, so an edit reaches a
        # session only through a rebuild.
        ("tools", "desktop_driver"),
        ("tools", "desktop_screen_width"),
        ("tools", "desktop_screen_height"),
        ("tools", "desktop_image_pin"),
        ("tools", "browser_image_pin"),
        # The browser tool resolves its settings once into a frozen model, so
        # the content-mode budget reaches a session only through a rebuild.
        ("tools", "browser_content_max_characters"),
        # The effective capability map is composed once, while the stakes
        # router's ``ModelResolver`` is assembled, and the resolver then answers
        # every routing question from that snapshot. An operator correcting a
        # mis-graded model therefore changes nothing a task can see until the
        # resolver is rebuilt, which is what this entry causes.
        ("providers", "capability_overrides"),
        # The evidence layer of that same map is graded during the same
        # assembly, so its boundaries reach a routing decision on exactly
        # the same terms: an operator narrowing what counts as expert, or
        # ageing out an older measurement, is re-composing the map.
        ("providers", "capability_evidence_expert_percentile"),
        ("providers", "capability_evidence_capable_percentile"),
        ("providers", "capability_evidence_max_age_days"),
        # Which sources count is read into the same composition. A source
        # switched off keeps its rows, so the only thing that stops them
        # grading is a rebuild reading the narrowed set.
        ("providers", "capability_sources"),
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
        # Each names the connection one runtime collaborator dispatches on,
        # and each is resolved into that collaborator while
        # ``build_runtime_services`` assembles it. Reassigning the model is
        # exactly the edit an operator expects to take effect on the next
        # run, so the rebuild is what makes the choice reach anything.
        ("engine", "evolution_proposer_model"),
        ("security", "grounding_model"),
        ("security", "vision_verify_model"),
        # The planning session's two spend bounds are resolved into a frozen
        # config while the coordinator is assembled, and the in-session check
        # reads that snapshot rather than the resolver. Without a rebuild the
        # operator's write binds the next process, not the next plan.
        ("coordination", "decomposition_agent_cost_ceiling"),
        ("budget", "session_token_ceiling"),
    }
)


def _trigger(pairs: Sequence[SettingChange]) -> str:
    """Render a rebuild trigger naming the writes that caused it.

    Args:
        pairs: The writes the batch carried.

    Returns:
        The batch label under the ``setting:`` prefix every rebuild trigger
        carries, so the log reads the same for a single write and a burst.
    """
    return f"setting:{describe_changes(pairs)}"


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

    @property
    def watched_keys(self) -> frozenset[SettingChange]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "runtime-reload"

    async def on_settings_changed(self, changes: Sequence[SettingChange]) -> None:
        """Rebuild the runtime services once for the whole batch.

        Args:
            changes: The watched writes this rebuild carries.

        Raises:
            Exception: Whatever the rebuild raised, after logging it, so the
                dispatcher records the failure with subscriber context.
        """
        from synthorg.workers.runtime_builder import (  # noqa: PLC0415
            reload_runtime_services,
        )

        if len(changes) > 1:
            logger.info(
                SETTINGS_RUNTIME_RELOAD_COALESCED,
                subscriber=self.subscriber_name,
                writes=len(changes),
            )
        try:
            await reload_runtime_services(self._app_state, trigger=_trigger(changes))
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="runtime_services",
                trigger=_trigger(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger=_trigger(changes),
            note="runtime services rebuilt",
        )
