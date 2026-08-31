# Strategy Module: Trendslop Mitigation

> Structural mitigation against LLM tendency to recommend trendy, context-insensitive strategies ("trendslop") for strategic agent roles.

**Module**: `src/synthorg/engine/strategy/`

Covers the core models, config, and prompt integration documented on this page.

---

## Background

Industry research shows LLMs systematically recommend trendy, context-insensitive strategies across 7 core business tensions. Prompt-level fixes produce only marginal bias reduction. SynthOrg mitigates this structurally through constitutional principles, multi-lens analysis, a confidence-calibration prompt instruction, and output mode control.

## Strategic Output Modes

Controls how strategic agents frame their recommendations. Set per-agent via `AgentIdentity.strategic_output_mode` or company-wide via `strategy.output_mode`.

| Mode | Behaviour | Default For |
|------|----------|-------------|
| `option_expander` | Present ALL options with lens analysis, no ranking | - |
| `advisor` | Recommend top 2-3 with reasoning and caveats | C-suite, VP |
| `decision_maker` | Make final recommendation with full justification | - |
| `context_dependent` | Resolves to decision_maker for the executive tier (role reporting depth <= 1), advisor otherwise | Director |

Resolution: agent override > config default. `context_dependent` resolves to `decision_maker` for C-suite/VP, `advisor` otherwise.

## Strategic Lenses

8 analysis perspectives forced on strategic agents:

### Default (always active)

| Lens | Purpose |
|------|---------|
| `contrarian` | Construct strongest argument for the opposite approach |
| `risk_focused` | Identify top risks, likelihood, impact, and mitigations |
| `cost_focused` | Calculate full cost including hidden costs, compare to status quo |
| `status_quo` | Evaluate whether current approach is adequate |

### Optional (enabled via config)

| Lens | Purpose |
|------|---------|
| `customer_focused` | Evaluate impact on end users |
| `competitive_response` | Anticipate competitor reactions |
| `implementation_feasibility` | Assess practical execution challenges |
| `historical_precedent` | Draw on historical patterns |

## Constitutional Principles

Anti-trendslop rules loaded from YAML packs and injected into system prompts. Each principle has an ID, text, category, and severity level (informational, warning, critical).

### Built-in Packs

| Pack | Focus | Principles |
|------|-------|------------|
| `default` | 7 HBR tensions (universal) | 7 |
| `startup` | Cash constraints, market fit, simplicity | 5 |
| `enterprise` | Exploitation, incremental change, compliance | 5 |
| `cost_sensitive` | ROI timelines, reversibility, efficiency | 5 |

### Pack Schema

```yaml
name: "pack-name"
version: "1.0.0"
description: "Pack description"
principles:
  - id: "principle_id"
    text: "Rule text injected into prompts"
    category: "category_name"
    severity: "critical"  # informational | warning | critical
```

User packs: `~/.synthorg/strategy-packs/<name>.yaml` (override builtins by name).

## Confidence Calibration

`prompt_injection.py` injects a fixed instruction asking strategic agents to
state, in their own recommendation text, a confidence level, an upside/downside
range, key assumptions, and what would change the recommendation. This is the
whole mechanism: it does not vary with `StrategyConfig.confidence.format` (the
`structured` / `narrative` / `both` / `probability` enum), and nothing parses
the agent's stated confidence back into a structured record. `impact.py` and
`confidence.py` -- the scorer and formatter that would have turned a
recommendation's risk profile and stated confidence into `ImpactScore` /
`ConfidenceMetadata` and attached them to a `DecisionRecord` -- had no
production caller and were removed. `StrategyConfig.cost_tier`,
`StrategyConfig.confidence.format`, and `StrategyConfig.progressive` are
consequently unconsumed: they parse and validate but select nothing.

## Prompt Injection

Strategic sections are injected into the system prompt after autonomy instructions, before the task section. Injection occurs when:

1. Agent has explicit `strategic_output_mode`, OR
2. The agent's role sits in the executive tier: reporting depth <= 1 (the CEO and its direct reports), via `role_depth(agent.role)`

### Injected Sections

1. **Strategic Analysis Framework**: maturity stage, industry, competitive position
2. **Constitutional Principles**: anti-trendslop rules from active pack
3. **Contrarian Analysis**: forced opposite-case consideration
4. **Confidence Calibration**: fixed instruction to state confidence, range, and assumptions
5. **Assumption Surfacing**: explicit assumption listing
6. **Output Requirements**: mode-specific output instructions

The strategy section is trimmable (removed first when over token budget).

## Config Shape

```yaml
strategy:
  output_mode: "advisor"
  cost_tier: "moderate"
  default_lenses:
    - contrarian
    - risk_focused
    - cost_focused
    - status_quo
  constitutional_principles:
    pack: "default"
    custom: []
  confidence:
    format: "structured"
  conflict_detection:
    strategy: "auto"
  context:
    source: "config"
    maturity_stage: "growth"
    industry: "technology"
    competitive_position: "challenger"
  progressive:
    weights:
      budget_impact: 0.2
      authority_level: 0.15
      decision_type: 0.15
      reversibility: 0.2
      blast_radius: 0.1
      time_horizon: 0.1
      strategic_alignment: 0.1
    thresholds:
      moderate: 0.4
      generous: 0.7
```

## Decision Records

`DecisionRecord.risk_card` is an optional `RiskCard` field (decision type,
reversibility, blast radius, time horizon). It is nullable and defaults to
`None`; no production path constructs a `DecisionRecord` with it populated
today. `ConfidenceMetadata` and `LensAttribution` -- the structured capture of
a recommendation's stated confidence and per-lens attribution -- had no
production caller and were removed along with `impact.py` / `confidence.py`.

## Architecture

### Protocol Pattern

The surviving major component is pluggable behind `@runtime_checkable Protocol`:

| Protocol | Implementations |
|----------|----------------|
| `StrategicContextProvider` | ConfigContextProvider, MemoryContextProvider, CompositeContextProvider |

`ImpactScorer` (CompositeImpactScorer, ExplicitImpactScorer,
HybridImpactScorer) and `ConfidenceFormatter` (StructuredFormatter,
NarrativeFormatter, BothFormatter, ProbabilityFormatter) had no production
caller and were removed with `impact.py` / `confidence.py`.

### Module Layout

```text
engine/strategy/
  __init__.py                    -- Public exports
  models.py                      -- Config + domain models (frozen Pydantic)
  lenses.py                      -- StrategicLens enum + definitions
  principles.py                  -- Pack loading service
  active_principle.py            -- Active-principle resolution
  active_principle_provider.py   -- Active-principle context provider
  principle_override_provider.py -- Per-scope principle overrides
  context.py                     -- Context providers
  strategic_context_provider.py  -- Context provider protocol
  adapter.py                     -- Strategy adapter for the engine
  scoping.py                     -- Scope resolution
  output.py                      -- Output mode handler
  prompt_injection.py            -- Prompt section builder
  packs/                         -- Built-in YAML principle packs
    default.yaml
    startup.yaml
    enterprise.yaml
    cost_sensitive.yaml
```

## References

- Prompt injection entry point: `src/synthorg/engine/strategy/prompt_injection.py`
