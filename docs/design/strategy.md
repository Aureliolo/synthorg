# Strategy Module: Trendslop Mitigation

> Structural mitigation against LLM tendency to recommend trendy, context-insensitive strategies ("trendslop") for strategic agent roles.

**Module**: `src/synthorg/engine/strategy/`
**Phase 1**: Core models, config, prompt integration (this page)
**Phase 2**: Meeting integration (#1158)

---

## Background

Industry research shows LLMs systematically recommend trendy, context-insensitive strategies across 7 core business tensions. Prompt-level fixes produce only marginal bias reduction. SynthOrg mitigates this structurally through constitutional principles, multi-lens analysis, confidence calibration, and output mode control.

## Strategic Output Modes

Controls how strategic agents frame their recommendations. Set per-agent via `AgentIdentity.strategic_output_mode` or company-wide via `strategy.output_mode`.

| Mode | Behavior | Default For |
|------|----------|-------------|
| `option_expander` | Present ALL options with lens analysis, no ranking | - |
| `advisor` | Recommend top 2-3 with reasoning and caveats | C-suite, VP |
| `decision_maker` | Make final recommendation with full justification | - |
| `context_dependent` | Resolves to advisor or decision_maker based on agent seniority | Director |

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

Strategic agents must provide calibrated confidence with every recommendation:

| Format | Output |
|--------|--------|
| `structured` | Labeled fields (confidence, range, assumptions, uncertainty) |
| `narrative` | Prose paragraph |
| `both` | Structured block + narrative |
| `probability` | Calibrated probability ranges with conditions |

## Impact Scoring

7-dimension weighted scoring determines the appropriate level of strategic analysis:

| Dimension | Default Weight | Source |
|-----------|---------------|--------|
| `budget_impact` | 0.20 | RiskCard / explicit |
| `authority_level` | 0.15 | Agent seniority |
| `decision_type` | 0.15 | RiskCard |
| `reversibility` | 0.20 | RiskCard |
| `blast_radius` | 0.10 | RiskCard |
| `time_horizon` | 0.10 | RiskCard |
| `strategic_alignment` | 0.10 | Context |

Weights must sum to 1.0. Composite score maps to cost tiers via thresholds.

## Cost Tier Resolution

| Tier | Composite Score | Analysis Depth |
|------|----------------|----------------|
| `minimal` | < 0.4 | Basic lens evaluation |
| `moderate` | 0.4 <= score < 0.7 | Full lens + constitutional review |
| `generous` | >= 0.7 | Full lens + constitutional + premortem |

Resolution: `ProgressiveTierResolver` (score-based) or `FixedTierResolver` (config-based).

## Prompt Injection

Strategic sections are injected into the system prompt after autonomy instructions, before the task section. Injection occurs when:

1. Agent has explicit `strategic_output_mode`, OR
2. Agent seniority is C-suite, VP, or Director

### Injected Sections

1. **Strategic Analysis Framework**: maturity stage, industry, competitive position
2. **Constitutional Principles**: anti-trendslop rules from active pack
3. **Contrarian Analysis**: forced opposite-case consideration
4. **Confidence Calibration**: output format requirements
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
  consensus_velocity:
    action: "devil_advocate"
    threshold: 0.85
  premortem:
    participants: "all"
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

`DecisionRecord` includes three optional strategy metadata fields.
All are nullable and default to `None` (or `()` for tuples); they
are only populated when the strategy module is active during the
meeting that produces the decision.

### RiskCard

Per-decision risk metadata (populated by premortem phase):
- `decision_type`: Type of decision
- `reversibility`: easily_reversible / moderate / locked_in
- `blast_radius`: individual / team / department / company_wide
- `time_horizon`: immediate / short_term / medium_term / long_term

### ConfidenceMetadata

Calibrated confidence for a recommendation:
- `level`: Point estimate (0.0-1.0)
- `range_lower`, `range_upper`: Confidence range
- `assumptions`: Key assumptions
- `uncertainty_factors`: Uncertainty sources

### LensAttribution

Which lens produced which insight (tuple, one per participant):
- `lens`: Lens name
- `insight`: Lens-specific insight
- `weight`: Influence on final recommendation

## Architecture

### Protocol Pattern

All major components are pluggable behind `@runtime_checkable Protocol`:

| Protocol | Implementations |
|----------|----------------|
| `StrategicContextProvider` | ConfigContextProvider, MemoryContextProvider, CompositeContextProvider |
| `ImpactScorer` | CompositeImpactScorer, ExplicitImpactScorer, HybridImpactScorer |
| `ConfidenceFormatter` | StructuredFormatter, NarrativeFormatter, BothFormatter, ProbabilityFormatter |
| `CostTierResolver` | FixedTierResolver, ProgressiveTierResolver |

### Module Layout

```text
engine/strategy/
  __init__.py         -- Public exports
  models.py           -- Config + domain models (frozen Pydantic)
  lenses.py           -- StrategicLens enum + definitions
  principles.py       -- Pack loading service
  context.py          -- Context providers
  impact.py           -- Impact scorers
  confidence.py       -- Confidence formatters
  output.py           -- Output mode handler
  tiers.py            -- Cost tier resolvers
  prompt_injection.py -- Prompt section builder
  packs/              -- Built-in YAML principle packs
    default.yaml
    startup.yaml
    enterprise.yaml
    cost_sensitive.yaml
```

## References

- Research: #693
- Phase 2 (meeting integration): #1158
- HBR trendslop article: March 2026
