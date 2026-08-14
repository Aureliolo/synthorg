# module-kind: code
"""Provision the per-feature models setup leaves blank.

Each per-feature model defaults to blank rather than to a placeholder, so
the feature it binds returns 503 until something chooses one. The wizard's
pickers prefill a recommendation but an operator can advance without
accepting it, so these helpers fill the gap from the matched roster before
the runtime rebuild on ``/setup/complete``.

Every value written is a bound ``{provider, model_id}`` reference lifted
from a roster agent's own assignment. There is deliberately no bare-model
path: a feature stays unset rather than resolving a provider on its own,
because the same model id reached through two connections is two different
calls. Only blank settings are written, so an explicit choice survives.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.types import CAPABILITY_LADDER, CapabilityLevel
from synthorg.llm.model_capability_policy import capability_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_FEATURE_MODEL_SELECT_FAILED,
    SETUP_FEATURE_MODEL_SELECTED,
    SETUP_FEATURE_MODEL_SKIPPED,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.service_protocol import SettingsServiceProtocol

logger = get_logger(__name__)

# Per-feature model settings auto-provisioned at setup, each paired with the
# prompt purpose whose capability (from the single capability policy) selects
# the model.
_PER_FEATURE_MODEL_SETTINGS: Final[tuple[tuple[str, str, PromptPurposeId], ...]] = (
    ("chief_of_staff", "chat_model", PromptPurposeId.COS_CHAT),
    ("chief_of_staff", "propose_model", PromptPurposeId.COS_PROPOSE),
    ("chief_of_staff", "routing_model", PromptPurposeId.COS_ROUTING),
    ("chief_of_staff", "narrative_model", PromptPurposeId.COS_NARRATIVE),
    ("charter", "interview_model", PromptPurposeId.CHARTER_INTERVIEW),
)

#: The rung the decomposition model is taken from. Decomposition sets the
#: shape every downstream task inherits, so it does not read a purpose
#: policy: it always asks for the top rung. Typed as the rung rather than a
#: bare string so a typo is a type error rather than a silent fall-through to
#: "any agent carrying a model".
_DECOMPOSITION_CAPABILITY: Final[CapabilityLevel] = CAPABILITY_LADDER[-1]


def _agent_model(agent: dict[str, object]) -> dict[str, object] | None:
    """Return an agent's assigned model dict when it carries a non-blank id.

    Returns:
        The ``model`` sub-dict, or ``None`` when no model is assigned.
    """
    model = agent.get("model")
    if isinstance(model, dict):
        model_id = model.get("model_id")
        if isinstance(model_id, str) and model_id.strip():
            return model
    return None


def _is_bound_model(model: dict[str, object]) -> bool:
    """Whether an assignment names both a provider and a model id.

    Returns:
        True when both halves are present and non-blank.
    """
    provider = model.get("provider")
    model_id = model.get("model_id")
    return bool(
        isinstance(provider, str)
        and provider.strip()
        and isinstance(model_id, str)
        and model_id.strip()
    )


def _agent_model_ref(agent: dict[str, object]) -> str | None:
    """Return an agent's model as a bound ``{provider, model_id}`` MODEL_REF.

    A settings write derived from a roster agent must carry the agent's own
    provider so no auto-resolution against "whichever provider serves the id"
    is ever possible. Returns ``None`` unless BOTH provider and model id are
    non-blank on the agent's assignment.

    Returns:
        A serialised bound model reference, or ``None``.
    """
    model = _agent_model(agent)
    if model is None or not _is_bound_model(model):
        return None
    return serialize_model_ref(
        ModelRef(
            provider=str(model["provider"]),
            model_id=str(model["model_id"]),
        )
    )


def _first_agent_with_model(
    agents: list[dict[str, object]],
    *,
    capability: CapabilityLevel | None,
    require_provider: bool = False,
) -> dict[str, object] | None:
    """First agent carrying a model, preferring one at *capability*.

    Args:
        agents: Roster agent dicts to search.
        capability: Preferred rung; matched agents are considered first.
        require_provider: When ``True``, skip agents whose model is not fully
            bound, so a ref-returning caller does not stop at an unusable
            agent when a later agent has a complete assignment.

    Returns:
        The chosen agent dict, or ``None`` when none carries a (bound) model.
    """
    preferred = (
        [a for a in agents if a.get("capability") == capability] if capability else []
    )
    for pool in (preferred, agents):
        for agent in pool:
            model = _agent_model(agent)
            if model is None:
                continue
            # Both halves, matching what ``_agent_model_ref`` demands: an agent
            # with a provider but no model id yields no ref, so accepting it
            # here would end the scan on a value the caller cannot use.
            if not require_provider or _is_bound_model(model):
                return agent
    return None


def pick_model_ref_for_capability(
    agents: list[dict[str, object]],
    capability: CapabilityLevel,
) -> str | None:
    """Choose a bound ``{provider, model_id}`` ref at *capability*, then any.

    Prefers an agent already matched to *capability* (so a per-feature model
    tracks the declared capability policy), falling back to any agent carrying
    a bound assignment.

    Returns:
        A serialised bound model reference, or ``None`` when no agent carries
        a bound (provider + model) assignment.
    """
    agent = _first_agent_with_model(
        agents,
        capability=capability,
        require_provider=True,
    )
    return _agent_model_ref(agent) if agent else None


def pick_decomposition_model_ref(agents: list[dict[str, object]]) -> str | None:
    """Choose a bound ``{provider, model_id}`` ref for the decomposition model.

    Returns:
        A serialised bound model reference, or ``None`` when no agent carries a
        bound assignment.
    """
    agent = _first_agent_with_model(
        agents,
        capability=_DECOMPOSITION_CAPABILITY,
        require_provider=True,
    )
    return _agent_model_ref(agent) if agent else None


async def _set_model_if_blank(
    settings_svc: SettingsServiceProtocol,
    namespace: str,
    key: str,
    model_ref: str | None,
) -> None:
    """Persist bound ref *model_ref* under ``namespace/key`` when blank.

    Compare-and-set on the token read alongside the value, because "write only
    when blank" is a read-modify-write and there is an await between the two
    halves. An operator choosing this model through ``PUT /settings`` in that
    window takes no lock this path shares, so an unconditional write would
    overwrite the very explicit choice this function exists to preserve. A
    losing CAS means somebody else filled the setting first, which is the
    outcome this function wanted anyway, so it is logged and not an error.
    """
    if model_ref is None:
        return
    current, token = await settings_svc.get_versioned(namespace, key)
    if current.strip():
        return
    try:
        await settings_svc.set(namespace, key, model_ref, expected_updated_at=token)
    except VersionConflictError:
        logger.info(
            SETUP_FEATURE_MODEL_SKIPPED,
            namespace=namespace,
            key=key,
            reason="set_concurrently",
        )
        return
    logger.info(
        SETUP_FEATURE_MODEL_SELECTED,
        namespace=namespace,
        key=key,
        model_ref=model_ref,
    )


async def ensure_per_feature_models(
    settings_svc: SettingsServiceProtocol,
) -> None:
    """Auto-fill the research + Chief-of-Staff + charter models when unset.

    The capability for each feature comes from the single capability policy
    (``capability_for_purpose``): research, charter and propose take an
    expert model, chat and narrator a capable one, routing a basic one.
    """
    from synthorg.api.controllers.setup_agents import (  # noqa: PLC0415
        get_existing_agents,
    )

    try:
        agents = await get_existing_agents(settings_svc)
        research_ref = pick_decomposition_model_ref(agents)
        await _set_model_if_blank(settings_svc, "research", "model", research_ref)
        for namespace, key, purpose in _PER_FEATURE_MODEL_SETTINGS:
            model_ref = pick_model_ref_for_capability(
                agents,
                capability_for_purpose(purpose),
            )
            await _set_model_if_blank(settings_svc, namespace, key, model_ref)
    except Exception as exc:
        # Deliberately not ``except*``: nothing here fans out through a
        # TaskGroup, and a bare re-raise inside an ``except*`` block raises the
        # singleton ExceptionGroup that PEP 654 wrapped around the original.
        # The API's typed handlers match on the exception class, so that
        # wrapper turns every DomainError raised below into a generic 500.
        reraise_critical(exc)
        logger.warning(
            SETUP_FEATURE_MODEL_SELECT_FAILED,
            note="per-feature model auto-fill failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise


__all__ = [
    "ensure_per_feature_models",
    "pick_decomposition_model_ref",
    "pick_model_ref_for_capability",
]
