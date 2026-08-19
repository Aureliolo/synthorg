# module-kind: code
"""What model a new hire would run on, proposed from what the org already has.

An operator asked to approve a hire is being asked to commit an agent to a
`(provider, model)` pair, and the pair is the most consequential half of that
decision: it decides what the agent can do and what it costs, for as long as
the agent exists. A single org-wide setting cannot answer it, because the
answer depends on the role being filled and on what the operator has actually
configured.

So the pair is PROPOSED, by the same capability matcher the setup wizard runs
when a template roster is filled out: a requirement is scored against the
operator's own configured models, and the winners are offered as a choice with
one recommended. Nothing here auto-picks a provider: every option names both
halves explicitly, every option came from the operator's own catalogue, and
the operator decides which one (or none).

The alternatives are the operator's OWN catalogue rather than a second opinion
derived from it. Re-running the matcher under different optimisation axes
looked like the richer answer and is not: the axes routinely converge on one
model, so an operator who wanted a different one would be offered three labels
for the same pair and no way to change it. What they can always answer is
"which of my models", so every tool-capable configured model is offered, the
matcher's own pick recommended among them.
"""

from collections.abc import Mapping
from typing import Final, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.config.model_metadata import is_tool_capable
from synthorg.config.schema import ProviderConfig
from synthorg.core.types import CapabilityLevel
from synthorg.hr.models import CandidateCard
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_HIRING_MODEL_PROPOSED
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.templates.model_matcher_config import (
    DEFAULT_MATCHER_CONFIG,
    ModelMatcherConfig,
    derive_capability,
)

logger = get_logger(__name__)

#: Ceiling on the fork's size. An operator picking a model wants a list they
#: can read, and a catalogue of forty would otherwise become forty options
#: nobody scrolls. The recommendation is always in it; the rest are the
#: cheapest, since a dearer model an operator specifically wants is exactly
#: the case they would rather set on the agent afterwards than scroll to here.
_MAX_OPTIONS: Final[int] = 8

#: Said of the pair the matcher itself chose.
_RECOMMENDED_SUMMARY: Final[str] = (
    "proposed for this role by the same capability matcher that fills out a "
    "template roster"
)

#: Said of every other configured model the role could run on.
_ALTERNATIVE_SUMMARY: Final[str] = "another of your configured models"


@runtime_checkable
class ProviderCatalogue(Protocol):
    """The operator's configured providers, read live when a hire is proposed.

    A protocol rather than the management service itself: the proposal needs
    the catalogue and nothing else, and a hire proposed at boot must not pull
    the whole provider-management surface into the hiring pipeline.
    """

    async def list_providers(self) -> Mapping[str, ProviderConfig]:
        """Return every configured provider, keyed by connection name."""
        ...


class HireModelOption(BaseModel):
    """One `(provider, model)` an operator may bind this hire to.

    Attributes:
        ref: The pair itself, both halves named.
        capability: The rung derived from the model's context window. A proxy
            the matcher reports and never selects on, so it is shown to an
            operator as one too.
        recommended: Whether the matcher chose this pair, and so whether this
            is the option approving without a choice takes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ref: ModelRef = Field(description="The provider + model pair")
    # The closed rung, not a free string. It comes from derive_capability and
    # the ladder compares it, so widening it to str drops the one thing the
    # checker could enforce about a value the roster ranks on.
    capability: CapabilityLevel = Field(description="Capability rung of the model")
    recommended: bool = Field(description="Whether the matcher chose this pair")

    @property
    def option_id(self) -> str:
        """The id the operator's pick travels back under.

        The serialised pair itself, so the choice needs no lookup table on the
        way home: an id that decodes to the binding cannot drift from the
        binding it named.

        Returns:
            The canonical ``MODEL_REF`` string for this option's pair.
        """
        return serialize_model_ref(self.ref)

    @property
    def label(self) -> str:
        """How the pair reads on the approval card.

        Returns:
            ``"<model id> via <provider>"``.
        """
        return f"{self.ref.model_id} via {self.ref.provider}"

    @property
    def summary(self) -> str:
        """Why this option is on the card, in the operator's terms.

        Returns:
            Whether the matcher proposed it or it is simply another model they
            configured, and the rung it sits at.
        """
        rationale = _RECOMMENDED_SUMMARY if self.recommended else _ALTERNATIVE_SUMMARY
        rung = f"; capability {self.capability}" if self.capability else ""
        return f"{rationale}{rung}"


class HireModelProposal(BaseModel):
    """Every pair this hire could be bound to, and why there might be none.

    Attributes:
        options: The offered pairs, recommended one first. Empty when no
            configured model clears the role's requirement.
        unmatched_reason: Why nothing was offered, stated in the operator's
            terms so the approval card can say it. ``None`` when options
            exist.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    options: tuple[HireModelOption, ...] = Field(default=())
    unmatched_reason: str | None = Field(default=None)

    @property
    def recommended(self) -> HireModelOption | None:
        """The option an approval with no explicit pick takes.

        Returns:
            The recommended option, or the first offered one when the org's
            own profile matched nothing, or ``None`` when nothing matched at
            all.
        """
        for option in self.options:
            if option.recommended:
                return option
        return self.options[0] if self.options else None


def _agent_for(candidate: CandidateCard) -> dict[str, object]:
    """Shape the candidate as the matcher's agent input.

    The matcher reads template agents, so a candidate is presented as one.
    Nothing is pinned and no axis is declared: scoring against the operator's
    own catalogue is the whole point, and a pin here would be this module
    choosing the model rather than proposing one.

    Args:
        candidate: The candidate being proposed.

    Returns:
        The agent mapping ``match_all_agents`` consumes.
    """
    return {
        "name": str(candidate.name),
        "role": str(candidate.role),
        "department": str(candidate.department),
    }


async def propose_hire_models(
    candidate: CandidateCard,
    *,
    catalogue: ProviderCatalogue | None,
    matcher_config: ModelMatcherConfig | None = None,
    org_profile: str = "balanced",
) -> HireModelProposal:
    """Offer the pairs this candidate could be hired onto.

    Args:
        candidate: The candidate being proposed.
        catalogue: The operator's configured providers, or ``None`` when the
            pipeline was built without one.
        matcher_config: Operator-tunable score weights, as the setup wizard
            resolves them. ``None`` uses the matcher defaults.
        org_profile: The company's own ``model_spend_profile``, which decides
            which optimisation axis is recommended. A value this map does not
            know recommends the neutral axis rather than nothing: the profile
            picks a default, and losing it must not cost the hire its options.

    Returns:
        The proposal. An empty one carries the reason, because "no model was
        proposed" and "the operator has configured no model this role can use"
        are different things to be told.
    """
    if catalogue is None:
        return HireModelProposal(
            unmatched_reason=(
                "No provider catalogue is wired, so no model could be proposed."
            )
        )
    providers = await catalogue.list_providers()
    if not providers:
        return HireModelProposal(
            unmatched_reason=(
                "No provider is configured, so there is no model to hire onto. "
                "Add a provider connection first."
            )
        )
    chosen = _matched_pair(
        candidate, providers, matcher_config=matcher_config, org_profile=org_profile
    )
    options = _offer(providers, matcher_config=matcher_config, chosen=chosen)
    if not options:
        return HireModelProposal(
            unmatched_reason=(
                "No configured model satisfies what this role needs. Every "
                "agent needs a tool-calling model: add or re-probe one, or "
                "re-enable a model that runtime tool-call failures downgraded."
            )
        )
    logger.info(
        HR_HIRING_MODEL_PROPOSED,
        candidate_role=str(candidate.role),
        option_count=len(options),
        org_profile=org_profile,
        matched=chosen is not None,
    )
    return HireModelProposal(options=options)


def _matched_pair(
    candidate: CandidateCard,
    providers: Mapping[str, ProviderConfig],
    *,
    matcher_config: ModelMatcherConfig | None,
    org_profile: str,
) -> ModelRef | None:
    """Ask the wizard's matcher which pair it would give this candidate.

    The matcher itself, not the wizard's controller around it: that wrapper
    adds a roster-starvation guard that raises when every agent it was given
    is unassigned, which for a one-agent call is simply "nothing matched" and
    is a recommendation the card can do without rather than a failed hire.

    Returns:
        The matched pair, or ``None`` when no configured model clears the
        role's requirement.
    """
    from synthorg.templates.model_matcher import (  # noqa: PLC0415
        _ProviderWithModels,
        match_all_agents,
    )

    # ``ProviderConfig`` structurally exposes ``models`` but its frozen field
    # is not assignable to the matcher protocol's mutable attribute; the cast
    # bridges the read-only/mutable gap at this read-only call, exactly as the
    # setup wizard's own call site does.
    pool = cast("Mapping[str, _ProviderWithModels]", providers)
    matches = match_all_agents(
        [_agent_for(candidate)],
        pool,
        matcher_config,
        model_spend_profile=org_profile,
    )
    if not matches:
        return None
    return ModelRef(provider=matches[0].provider_name, model_id=matches[0].model_id)


def _offer(
    providers: Mapping[str, ProviderConfig],
    *,
    matcher_config: ModelMatcherConfig | None,
    chosen: ModelRef | None,
) -> tuple[HireModelOption, ...]:
    """Offer every configured model an agent could run on, matcher's pick first.

    Filtered on tool capability alone, because that is the one hard property
    an agent's model must have: an agent bound to a model that cannot call a
    tool fails every dispatch it is ever given. Everything else is preference,
    and preference is the operator's to exercise here.

    Ordered cheapest first behind the recommendation, so a truncated list
    keeps the options an operator most plausibly wanted.

    Returns:
        The offered pairs, capped, recommended first.
    """
    cfg = matcher_config if matcher_config is not None else DEFAULT_MATCHER_CONFIG
    eligible: list[tuple[float, str, HireModelOption]] = []
    for name, provider in providers.items():
        for model in provider.models:
            if not is_tool_capable(model.metadata):
                continue
            ref = ModelRef(provider=name, model_id=str(model.id))
            eligible.append(
                (
                    model.cost_per_1k_output,
                    str(model.id),
                    HireModelOption(
                        ref=ref,
                        capability=derive_capability(model, cfg),
                        recommended=chosen is not None and ref == chosen,
                    ),
                )
            )
    # Cost then id: the id is what breaks a tie between free models, and
    # without it the order depends on dict iteration and the same catalogue
    # offers a different list every time it is read.
    eligible.sort(key=lambda entry: (entry[0], entry[1]))
    ordered = [entry[2] for entry in eligible]
    ordered.sort(key=lambda option: not option.recommended)
    return tuple(ordered[:_MAX_OPTIONS])


__all__ = [
    "HireModelOption",
    "HireModelProposal",
    "ProviderCatalogue",
    "propose_hire_models",
]
