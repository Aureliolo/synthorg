"""Tests for the capability-aware model-matching engine."""

from datetime import date

import pytest
from pydantic import ValidationError

from synthorg.config.model_metadata import MetadataSource, ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.templates.model_matcher import (
    CapabilityFitStrategy,
    ModelMatch,
    ModelSelectionStrategy,
    get_model_selection_strategy,
    match_all_agents,
    match_model,
)
from synthorg.templates.model_matcher_config import ModelMatcherConfig, derive_tier
from synthorg.templates.model_matcher_tiering import passes_hard_filters
from synthorg.templates.model_requirements import ModelRequirement

_CFG = ModelMatcherConfig()


def _make_model(  # noqa: PLR0913 -- keyword-only test factory
    model_id: str,
    *,
    max_context: int = 200_000,
    cost_input: float = 0.01,
    latency_ms: int | None = None,
    # Deliberately the opposite of ``ModelMetadata.supports_tools``'s
    # conservative production default: tool calling is an unconditional match
    # floor, so a tool-less default would exclude every fixture and couple
    # unrelated vision/family/priority tests to tool-calling behaviour. Tests
    # that exercise the floor pass ``tools=False`` explicitly.
    tools: bool = True,
    tool_calls_verified: bool | None = None,
    vision: bool = False,
    reasoning: bool = False,
    embeddings: bool = False,
    family: str | None = None,
    generation: float | None = None,
    parameter_count: int | None = None,
    release_date: date | None = None,
    source: MetadataSource = "litellm",
) -> ProviderModelConfig:
    """Factory for test ProviderModelConfig with metadata."""
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=cost_input,
        max_context=max_context,
        estimated_latency_ms=latency_ms,
        metadata=ModelMetadata(
            supports_tools=tools,
            tool_calls_verified=tool_calls_verified,
            supports_vision=vision,
            supports_reasoning=reasoning,
            supports_embeddings=embeddings,
            family=family,
            generation=generation,
            parameter_count=parameter_count,
            release_date=release_date,
            metadata_source=source,
        ),
    )


class _Provider:
    """Minimal provider exposing a typed ``models`` tuple."""

    def __init__(
        self,
        models: tuple[ProviderModelConfig, ...],
        base_url: str | None = None,
        *,
        agent_eligible: bool = True,
    ) -> None:
        self.models = models
        self.base_url = base_url
        self.agent_eligible = agent_eligible


def _provider(*models: ProviderModelConfig) -> _Provider:
    """Build a ``_Provider`` from positional models."""
    return _Provider(models)


# ── Hard capability filters ──────────────────────────────────


@pytest.mark.unit
class TestHardFilters:
    def test_vision_requirement_excludes_non_vision(self) -> None:
        no_vision = _make_model("plain", vision=False)
        has_vision = _make_model("seer", vision=True)
        req = ModelRequirement(requires_vision=True)
        model, _ = match_model(req, (no_vision, has_vision))
        assert model is not None
        assert model.id == "seer"

    @pytest.mark.parametrize(
        ("tools", "verified", "source", "admitted"),
        [
            pytest.param(False, None, "litellm", False, id="known-incapable"),
            pytest.param(True, None, "litellm", True, id="known-capable"),
            pytest.param(False, None, "unknown", True, id="unprobed-optimism"),
            pytest.param(
                True, False, "unknown", False, id="runtime-failed-beats-optimism"
            ),
            pytest.param(
                True, False, "litellm", False, id="runtime-failed-beats-claim"
            ),
            pytest.param(False, True, "litellm", True, id="runtime-proven-beats-stale"),
            pytest.param(True, True, "litellm", True, id="runtime-proven-and-claimed"),
        ],
    )
    def test_tool_calling_floor_decision_matrix(
        self,
        tools: bool,
        verified: bool | None,
        source: MetadataSource,
        admitted: bool,
    ) -> None:
        # Tool calling is a floor, not an opt-in, so a bare requirement that
        # never mentions tools still drives every one of these outcomes.
        # Asserted against the filter itself rather than through match_model:
        # a selection result would only prove exclusion by way of the scorer's
        # tie-break, which is unrelated machinery that could change.
        candidate = _make_model(
            "candidate", tools=tools, tool_calls_verified=verified, source=source
        )
        assert passes_hard_filters(candidate, ModelRequirement()) is admitted

    def test_tool_calling_floor_excludes_non_tools_leaving_capable_sibling(
        self,
    ) -> None:
        # The floor is a hard exclusion, not a scoring preference: the plain
        # model is removed from the pool, not merely out-ranked.
        plain = _make_model("plain", tools=False)
        caller = _make_model("caller", tools=True)
        assert passes_hard_filters(plain, ModelRequirement()) is False
        model, _ = match_model(ModelRequirement(), (plain, caller))
        assert model is not None
        assert model.id == "caller"

    def test_tool_calling_floor_leaves_agent_unmatched_when_pool_empties(self) -> None:
        model, score = match_model(
            ModelRequirement(), (_make_model("plain", tools=False),)
        )
        assert model is None
        assert score == 0.0

    def test_embedding_model_never_assigned_to_chat_agent(self) -> None:
        # An embedding model produces vectors, not chat completions, so it is
        # excluded even for a requirement with no capability flags set.
        embedder = _make_model("embed", embeddings=True)
        model, score = match_model(ModelRequirement(), (embedder,))
        assert model is None
        assert score == 0.0

    def test_embedding_excluded_when_chat_model_available(self) -> None:
        embedder = _make_model("embed", embeddings=True)
        chat = _make_model("chat")
        model, _ = match_model(ModelRequirement(), (embedder, chat))
        assert model is not None
        assert model.id == "chat"

    def test_runtime_failed_model_excluded_for_every_agent(self) -> None:
        # No role is exempt: a model runtime-proven incapable is dropped from
        # the pool even for a requirement declaring no capability at all, and
        # a healthy sibling is what the agent gets instead.
        downgraded = _make_model(
            "downgraded", tools=True, tool_calls_verified=False, source="unknown"
        )
        chat = _make_model("chat")
        assert passes_hard_filters(downgraded, ModelRequirement()) is False
        model, _ = match_model(ModelRequirement(), (downgraded, chat))
        assert model is not None
        assert model.id == "chat"

    def test_reasoning_requirement_honoured(self) -> None:
        thinker = _make_model("thinker", reasoning=True)
        plain = _make_model("plain", reasoning=False)
        req = ModelRequirement(requires_reasoning=True)
        model, _ = match_model(req, (plain, thinker))
        assert model is not None
        assert model.id == "thinker"

    def test_min_context_filter(self) -> None:
        small = _make_model("small", max_context=8_000)
        big = _make_model("big", max_context=200_000)
        req = ModelRequirement(min_context=100_000)
        model, _ = match_model(req, (small, big))
        assert model is not None
        assert model.id == "big"

    def test_unknown_metadata_optimistic_when_required(self) -> None:
        # Optimistic: an unknown-source model is ALLOWED when a capability is
        # required (most modern models are capable) rather than fail-closed,
        # so an un-probed cloud model is usable instead of leaving the agent
        # unassigned.
        unknown = _make_model("legacy", vision=False, source="unknown")
        req = ModelRequirement(requires_vision=True)
        model, score = match_model(req, (unknown,))
        assert model is not None
        assert model.id == "legacy"
        assert score > 0.0

    def test_proven_capability_outranks_unknown(self) -> None:
        # Prefer-proven: a model with proven capabilities ranks above an
        # unknown-source model for the same requirement.
        proven = _make_model("proven", vision=True, source="litellm")
        unknown = _make_model("legacy", vision=False, source="unknown")
        req = ModelRequirement(requires_vision=True)
        model, _ = match_model(req, (unknown, proven))
        assert model is not None
        assert model.id == "proven"

    def test_unknown_metadata_ok_when_not_required(self) -> None:
        unknown = _make_model("legacy", source="unknown")
        req = ModelRequirement()
        model, _ = match_model(req, (unknown,))
        assert model is not None
        assert model.id == "legacy"


# ── Family / pattern resolution ──────────────────────────────


@pytest.mark.unit
class TestFamilyResolution:
    def test_family_pins_newest_generation(self) -> None:
        old = _make_model("ex-1", family="example-large", generation=1.0)
        new = _make_model("ex-2", family="example-large", generation=2.0)
        other = _make_model("other", family="example-small", generation=9.0)
        req = ModelRequirement(family="example-large")
        model, _ = match_model(req, (old, new, other))
        assert model is not None
        assert model.id == "ex-2"

    def test_family_never_crosses_failed_capability_gate(self) -> None:
        # Newest in family lacks vision; older one has it.
        newest_no_vision = _make_model(
            "ex-2", family="example-large", generation=2.0, vision=False
        )
        older_vision = _make_model(
            "ex-1", family="example-large", generation=1.0, vision=True
        )
        req = ModelRequirement(family="example-large", requires_vision=True)
        model, _ = match_model(req, (newest_no_vision, older_vision))
        assert model is not None
        assert model.id == "ex-1"

    def test_pattern_pins_newest_matching_id(self) -> None:
        a = _make_model("example-x-1", generation=1.0)
        b = _make_model("example-x-2", generation=2.0)
        c = _make_model("other-9", generation=9.0)
        req = ModelRequirement(model_pattern="example-x-*")
        model, _ = match_model(req, (a, b, c))
        assert model is not None
        assert model.id == "example-x-2"

    def test_family_miss_falls_back_to_survivors(self) -> None:
        m = _make_model("only", family="example-large", generation=1.0)
        req = ModelRequirement(family="nonexistent-family")
        model, score = match_model(req, (m,))
        assert model is not None
        assert model.id == "only"
        assert score > 0.0

    def test_family_never_crosses_the_tool_calling_floor(self) -> None:
        # A family ref pins the newest HARD-FILTER SURVIVOR, so the floor
        # applies before the pin: the newest sibling is skipped for the older
        # one that can still call tools.
        newest_incapable = _make_model(
            "ex-2", family="example-large", generation=2.0, tool_calls_verified=False
        )
        older_capable = _make_model("ex-1", family="example-large", generation=1.0)
        req = ModelRequirement(family="example-large")
        model, _ = match_model(req, (newest_incapable, older_capable))
        assert model is not None
        assert model.id == "ex-1"

    def test_pattern_never_crosses_the_tool_calling_floor(self) -> None:
        newest_incapable = _make_model(
            "example-x-2", generation=2.0, tool_calls_verified=False
        )
        older_capable = _make_model("example-x-1", generation=1.0)
        req = ModelRequirement(model_pattern="example-x-*")
        model, _ = match_model(req, (newest_incapable, older_capable))
        assert model is not None
        assert model.id == "example-x-1"

    def test_newest_breaks_generation_tie_by_release_date(self) -> None:
        older = _make_model(
            "ex-a", family="f", generation=2.0, release_date=date(2025, 1, 1)
        )
        newer = _make_model(
            "ex-b", family="f", generation=2.0, release_date=date(2025, 6, 1)
        )
        req = ModelRequirement(family="f")
        model, _ = match_model(req, (older, newer))
        assert model is not None
        assert model.id == "ex-b"


# ── Priority axis (absolute, not cost-thirds) ────────────────


@pytest.mark.unit
class TestPriorityAxis:
    def test_cost_priority_picks_cheapest(self) -> None:
        cheap = _make_model("cheap", cost_input=0.001)
        mid = _make_model("mid", cost_input=0.01)
        dear = _make_model("dear", cost_input=0.1)
        req = ModelRequirement(priority="cost")
        model, _ = match_model(req, (dear, mid, cheap))
        assert model is not None
        assert model.id == "cheap"

    def test_quality_priority_picks_newest_generation(self) -> None:
        g1 = _make_model("g1", generation=1.0)
        g3 = _make_model("g3", generation=3.0)
        req = ModelRequirement(priority="quality")
        model, _ = match_model(req, (g1, g3))
        assert model is not None
        assert model.id == "g3"

    def test_quality_priority_prefers_larger_parameter_count(self) -> None:
        # Parameter count is the dominant strength signal: a 700B frontier
        # model beats a small local one for a quality-priority agent.
        small = _make_model("small", parameter_count=26_000_000_000)
        frontier = _make_model("frontier", parameter_count=700_000_000_000)
        req = ModelRequirement(priority="quality")
        model, _ = match_model(req, (small, frontier))
        assert model is not None
        assert model.id == "frontier"

    def test_speed_priority_picks_lowest_latency(self) -> None:
        slow = _make_model("slow", latency_ms=2_000)
        fast = _make_model("fast", latency_ms=200)
        req = ModelRequirement(priority="speed")
        model, _ = match_model(req, (slow, fast))
        assert model is not None
        assert model.id == "fast"

    def test_speed_priority_deprioritises_unknown_latency(self) -> None:
        slow = _make_model("slow", latency_ms=5_000)
        unknown = _make_model("unknown", latency_ms=None)
        req = ModelRequirement(priority="speed")
        model, _ = match_model(req, (unknown, slow))
        # A model with a real (even slow) latency beats unknown latency.
        assert model is not None
        assert model.id == "slow"

    def test_balanced_priority_blends_quality_and_cost(self) -> None:
        # Pool-normalised blend: the mid model beats both the dear-but-newest
        # and the cheap-but-oldest extremes (which tie). Without normalisation
        # the raw gen-minus-cost formula would just pick the newest.
        quality = _make_model("dear-new", generation=3.0, cost_input=0.1)
        cheap = _make_model("cheap-old", generation=1.0, cost_input=0.001)
        middle = _make_model("mid", generation=2.0, cost_input=0.01)
        req = ModelRequirement(priority="balanced")
        model, _ = match_model(req, (quality, cheap, middle))
        assert model is not None
        assert model.id == "mid"


# ── Derived tier + score bounds ──────────────────────────────


@pytest.mark.unit
class TestDeriveTierAndScore:
    def test_derive_tier_bands(self) -> None:
        assert derive_tier(_make_model("a", max_context=200_000), _CFG) == "large"
        assert derive_tier(_make_model("b", max_context=64_000), _CFG) == "medium"
        assert derive_tier(_make_model("c", max_context=8_000), _CFG) == "small"

    def test_score_within_bounds(self) -> None:
        models = (
            _make_model("a", tools=True, vision=True, reasoning=True),
            _make_model("b"),
        )
        _, score = match_model(ModelRequirement(min_context=1_000), models)
        assert 0.0 <= score <= 1.0

    def test_empty_available_returns_none(self) -> None:
        model, score = match_model(ModelRequirement(), ())
        assert model is None
        assert score == 0.0


# ── Batch matching ───────────────────────────────────────────


@pytest.mark.unit
class TestMatchAllAgents:
    def test_assigns_and_derives_tier_from_selected_model(self) -> None:
        providers = {"prov": _provider(_make_model("big", max_context=200_000))}
        agents: list[dict[str, object]] = [{}]
        matches = match_all_agents(agents, providers)
        assert len(matches) == 1
        assert matches[0].model_id == "big"
        # Tier is report-only, derived from the SELECTED model's metadata.
        assert matches[0].tier == "large"

    def test_omits_agent_when_no_capability_match(self) -> None:
        # Requires vision but no provider model has it -> fail-closed: the
        # agent is omitted rather than assigned a non-compliant model.
        providers = {"prov": _provider(_make_model("plain", vision=False))}
        agents = [{"model_requirement": {"requires_vision": True}}]
        matches = match_all_agents(agents, providers)
        assert matches == []

    def test_selects_compliant_model_in_another_provider(self) -> None:
        # The first provider's model fails the hard filter; the matcher
        # still finds the compliant model in the second provider.
        providers = {
            "alpha": _provider(_make_model("alpha-1", vision=False)),
            "beta": _provider(_make_model("beta-1", vision=True)),
        }
        agents = [{"model_requirement": {"requires_vision": True}}]
        matches = match_all_agents(agents, providers)
        assert len(matches) == 1
        assert matches[0].provider_name == "beta"
        assert matches[0].model_id == "beta-1"

    def test_excludes_agent_ineligible_provider_from_the_pool(self) -> None:
        # An agent-ineligible provider (e.g. a feature-only gateway) is kept out
        # of the seeding pool: the matcher assigns the eligible provider's model
        # even though the ineligible one would also match.
        providers = {
            "gateway": _Provider((_make_model("gateway-1"),), agent_eligible=False),
            "eligible": _Provider((_make_model("eligible-1"),), agent_eligible=True),
        }
        matches = match_all_agents([{}], providers)
        assert len(matches) == 1
        assert matches[0].provider_name == "eligible"
        assert matches[0].model_id == "eligible-1"

    def test_all_providers_ineligible_omits_agent(self) -> None:
        # With every provider ineligible for agents, the pool is empty and the
        # agent is omitted (fail-closed) rather than sourced from a gateway.
        providers = {
            "gateway": _Provider((_make_model("gateway-1"),), agent_eligible=False),
        }
        assert match_all_agents([{}], providers) == []

    def test_no_models_anywhere_omits_agent(self) -> None:
        providers = {"prov": _provider()}
        matches = match_all_agents([{}], providers)
        assert matches == []

    def test_returns_model_match_instances(self) -> None:
        providers = {"prov": _provider(_make_model("m"))}
        matches = match_all_agents([{}], providers)
        assert all(isinstance(m, ModelMatch) for m in matches)

    def test_embedding_only_provider_omits_agent(self) -> None:
        # The batch path must apply the same embedder exclusion as match_model:
        # a pool of only embedding models leaves the chat agent unassigned.
        providers = {"prov": _provider(_make_model("embed", embeddings=True))}
        matches = match_all_agents([{}], providers)
        assert matches == []

    def test_embedding_excluded_when_chat_model_present(self) -> None:
        providers = {
            "prov": _provider(
                _make_model("embed", embeddings=True),
                _make_model("chat"),
            )
        }
        matches = match_all_agents([{}], providers)
        assert len(matches) == 1
        assert matches[0].model_id == "chat"


# ── Explicit model-id pin ────────────────────────────────────


@pytest.mark.unit
class TestExplicitModelId:
    def test_pins_exact_id_over_scoring(self) -> None:
        cheap = _make_model("cheap", cost_input=0.001, generation=9.0)
        pinned = _make_model("pinned", cost_input=0.5, generation=1.0)
        req = ModelRequirement(model_id="pinned")
        model, score = match_model(req, (cheap, pinned))
        assert model is not None
        assert model.id == "pinned"
        assert score == 1.0

    def test_pins_by_alias(self) -> None:
        aliased = ProviderModelConfig(id="full-id-001", alias="fast")
        req = ModelRequirement(model_id="fast")
        model, _ = match_model(req, (aliased,))
        assert model is not None
        assert model.id == "full-id-001"

    def test_pin_absent_returns_none(self) -> None:
        req = ModelRequirement(model_id="missing")
        model, score = match_model(req, (_make_model("present"),))
        assert model is None
        assert score == 0.0

    def test_pin_bypasses_capability_filter(self) -> None:
        # An explicit pin is honoured even if it lacks a capability that a
        # bare requirement would hard-filter on (the user chose this model).
        plain = _make_model("plain", vision=False)
        req = ModelRequirement(model_id="plain", requires_vision=True)
        model, _ = match_model(req, (plain,))
        assert model is not None
        assert model.id == "plain"

    def test_pin_does_not_bypass_the_tool_calling_floor(self) -> None:
        # The one thing a pin cannot override. A model that has PROVEN at
        # runtime it cannot call tools would leave the agent emitting prose
        # and failing every task that expects an artifact, so the pin is
        # refused outright rather than seeding an agent that cannot work.
        proven_incapable = _make_model("pinned", tools=True, tool_calls_verified=False)
        req = ModelRequirement(model_id="pinned")
        model, score = match_model(req, (proven_incapable,))
        assert model is None
        assert score == 0.0

    def test_pin_admits_an_unprobed_model(self) -> None:
        # The floor is optimistic: an un-enriched model is not yet KNOWN to
        # lack tool calling, so the pin still resolves.
        unprobed = _make_model("pinned", tools=False, source="unknown")
        req = ModelRequirement(model_id="pinned")
        model, _ = match_model(req, (unprobed,))
        assert model is not None
        assert model.id == "pinned"

    def test_pin_skips_a_tool_incapable_alias_sibling(self) -> None:
        # One alias can resolve to several catalogue entries; the floor picks
        # among them rather than failing the pin outright.
        incapable = ProviderModelConfig(
            id="broken-001",
            alias="fast",
            metadata=ModelMetadata(supports_tools=True, tool_calls_verified=False),
        )
        capable = ProviderModelConfig(
            id="working-001",
            alias="fast",
            metadata=ModelMetadata(supports_tools=True, metadata_source="litellm"),
        )
        req = ModelRequirement(model_id="fast")
        model, _ = match_model(req, (incapable, capable))
        assert model is not None
        assert model.id == "working-001"

    def test_pinned_tool_incapable_model_leaves_the_agent_unassigned(self) -> None:
        # The batch path inherits the floor: no model is better than one that
        # cannot do the work, so the agent is omitted from the roster.
        providers = {
            "prov": _provider(_make_model("pinned", tool_calls_verified=False))
        }
        agents = [{"model_requirement": {"model_id": "pinned"}}]
        assert match_all_agents(agents, providers) == []


# ── Strategy seam ────────────────────────────────────────────


@pytest.mark.unit
class TestStrategySeam:
    def test_default_strategy_is_capability_fit(self) -> None:
        assert isinstance(get_model_selection_strategy(), CapabilityFitStrategy)

    def test_default_strategy_satisfies_protocol(self) -> None:
        assert isinstance(get_model_selection_strategy(), ModelSelectionStrategy)

    def test_custom_strategy_is_used(self) -> None:
        sentinel = _make_model("sentinel")

        class _FixedStrategy:
            def select(
                self,
                requirement: ModelRequirement,
                candidates: object,
                config: ModelMatcherConfig,
            ) -> tuple[ProviderModelConfig, float]:
                return sentinel, 1.0

        model, score = match_model(
            ModelRequirement(requires_vision=True),
            (_make_model("ignored"),),
            strategy=_FixedStrategy(),
        )
        assert model is sentinel
        assert score == 1.0


# ── Config projection ────────────────────────────────────────


@pytest.mark.unit
class TestModelMatcherConfig:
    def test_from_bridge_config_projects_fields(self) -> None:
        from synthorg.settings.bridge_configs import EngineBridgeConfig

        bridge = EngineBridgeConfig()
        cfg = ModelMatcherConfig.from_bridge_config(bridge)
        assert cfg.base_score == bridge.matcher_base_score
        assert cfg.capability_fit_weight == bridge.matcher_capability_fit_weight
        assert cfg.headroom_max_bonus == bridge.matcher_headroom_max_bonus
        assert cfg.priority_max_bonus == bridge.matcher_priority_max_bonus
        assert cfg.headroom_ratio_cap == bridge.matcher_headroom_ratio_cap
        assert cfg.tier_large_min_context == bridge.matcher_tier_large_min_context
        assert cfg.tier_medium_min_context == bridge.matcher_tier_medium_min_context
        assert cfg.min_usable_parameters == bridge.matcher_min_usable_parameters
        assert cfg.prefer_local == bridge.matcher_prefer_local
        assert cfg.min_cloud_tier == bridge.matcher_min_cloud_tier

    @pytest.mark.parametrize("bad_tier", [0, 5])
    def test_min_cloud_tier_out_of_range_rejected(self, bad_tier: int) -> None:
        with pytest.raises(ValidationError):
            ModelMatcherConfig(min_cloud_tier=bad_tier)
