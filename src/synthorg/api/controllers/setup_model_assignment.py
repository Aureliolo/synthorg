# module-kind: code
"""Assign a configured model to each template agent during setup.

Split from ``setup_agents`` so the assignment pass, and the roster-level
starvation guard that closes it, stay readable next to each other rather
than buried among the wizard's serialisation helpers.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from synthorg.config.schema import ProviderConfig
from synthorg.core.domain_errors import ProviderModelCoverageInsufficientError
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_MODEL_ASSIGNMENT_INCOMPLETE,
    SETUP_MODEL_FALLBACK_USED,
)
from synthorg.templates.model_matcher_config import ModelMatcherConfig

if TYPE_CHECKING:
    # Only referenced inside a ``cast`` string annotation, so the import
    # never resolves at runtime: keep it guarded to avoid importing a private
    # type across the package boundary.
    from synthorg.templates.model_matcher import _ProviderWithModels

logger = get_logger(__name__)


def match_and_assign_models(
    agents: list[dict[str, object]],
    providers: Mapping[str, ProviderConfig],
    matcher_config: ModelMatcherConfig | None = None,
    *,
    model_spend_profile: str = "balanced",
) -> list[dict[str, object]]:
    """Auto-assign models to template agents using the matching engine.

    Returns a new list of agent dicts with ``model.provider`` and
    ``model.model_id`` set to the best available match.  The input
    list is not modified.

    Args:
        agents: Expanded agent config dicts from ``expand_template_agents``.
        providers: Provider name -> config mapping.
        matcher_config: Optional :class:`ModelMatcherConfig` carrying
            operator-tunable score weights resolved from
            ``EngineBridgeConfig``. ``None`` falls back to the matcher
            defaults that mirror the historical hardcoded values.
        model_spend_profile: Company model-tier profile ('economy' | 'balanced' |
            'premium') biasing every agent's priority cheaper or stronger;
            'balanced' leaves the template's per-agent priorities intact.

    Returns:
        New list of agent dicts with model assignments applied.

    Raises:
        ProviderModelCoverageInsufficientError: When every agent ends up
            unassigned, so the roster could not do any work.
    """
    from synthorg.templates.model_matcher import match_all_agents  # noqa: PLC0415

    # ProviderConfig structurally exposes ``models`` but its frozen field
    # is not assignable to the matcher protocol's mutable attribute; the
    # cast bridges the read-only/mutable gap at this read-only call.
    matches = match_all_agents(
        agents,
        cast("Mapping[str, _ProviderWithModels]", providers),
        matcher_config,
        model_spend_profile=model_spend_profile,
    )
    match_map = {
        m.agent_index: {
            "provider": m.provider_name,
            "model_id": m.model_id,
            "capability": m.capability,
        }
        for m in matches
    }
    result: list[dict[str, object]] = []
    unassigned = 0
    for idx, agent in enumerate(agents):
        if idx in match_map:
            # ``capability`` is report-only, derived from the selected model's
            # metadata; round-trips to the UI via ``AgentConfig.capability``.
            assigned = match_map[idx]
            result.append(
                {**agent, "model": assigned, "capability": assigned["capability"]},
            )
        else:
            # The matcher is fail-closed: an agent whose hard capability
            # requirement no configured model satisfies gets no match and is
            # left unassigned here. The pre-flight provider gate only catches
            # "no models at all", so a catalogue that has models but none the
            # floor accepts reaches this branch instead -- loud enough to see.
            logger.warning(
                SETUP_MODEL_FALLBACK_USED,
                agent_index=idx,
                agent_name=agent.get("name", ""),
                capability=agent.get("capability", ""),
                reason="no_match_returned",
            )
            unassigned += 1
            result.append(dict(agent))

    _guard_roster_starvation(unassigned, len(agents))
    return result


def _guard_roster_starvation(unassigned: int, total: int) -> None:
    """Report unassigned agents, and refuse a roster that has none assigned.

    Args:
        unassigned: How many agents finished matching with no model.
        total: How many agents were matched in all.

    Raises:
        ProviderModelCoverageInsufficientError: When every agent is unassigned.
    """
    if unassigned:
        logger.warning(
            SETUP_MODEL_ASSIGNMENT_INCOMPLETE,
            unassigned_count=unassigned,
            total_agents=total,
        )
    if total and unassigned == total:
        # The pre-flight gate only rejects an empty catalogue, so a catalogue
        # whose models all fail the capability floors lands here instead. Same
        # remediation as that gate, so it raises the same error and the wizard
        # keeps its "go back to Providers" affordance.
        msg = (
            "No configured provider model satisfies the agent requirements, so "
            "every agent would start unassigned and unable to do work. Every "
            "agent needs a tool-calling model: add or re-probe one, or "
            "re-enable a model that runtime tool-call failures downgraded."
        )
        raise ProviderModelCoverageInsufficientError(msg)
