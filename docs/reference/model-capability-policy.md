---
title: Model Capability Policy
description: The purpose-to-capability convention that pins each prompt class to a design rung, and how the pin-validation benchmark consumes it.
---

# Model Capability Policy

A model pin records a **design capability**, not a vendor model. SynthOrg is
provider-agnostic: no canonical vendor model is privileged, so a prompt
class pins one of the vendor-agnostic archetypes
(`example-expert-001`, `example-capable-001`, `example-basic-001`) that
`heuristic_capability` (in `synthorg.budget.model_capability`) resolves. This
page documents which rung each system prompt class is pinned to and the
reasoning behind it.

The policy lives in `synthorg.llm.model_capability_policy`. It maps every
`PromptPurposeId` in the prompt-purpose registry
(`synthorg.llm.prompt_purpose`) to a rung, with an import-time guard that
rejects any purpose missing an entry. The
[pin-validation benchmark](#pin-validation-benchmark) consumes
the policy to validate each prompt class against its pinned rung, and the
per-class `ModelPinMetadata` rollout assigns its rungs from it.

## The roster field is a cache; the registry decides

Two things claim to know a model's capability: `AgentIdentity.model.capability`,
written onto the roster when the agent was staffed, and the model catalogue the
capability policy reads. They disagree the moment an operator re-grades a model,
and only one of them can be right.

The catalogue decides. `ResolvedAgentCapabilityReader` resolves the agent's own
`(provider, model)` pair through `resolve_for_pair` and treats that rung as
authoritative, falling back to the roster's own claim only for a pair the
catalogue does not serve or has not graded. The roster field is therefore a
cache, never the answer.

## The ladder moves the agent, never the model

`CapabilityPolicy` (`engine/routing_policy/capability_policy.py`) is the one
object that answers every capability question the loop asks: what rung the work
demands (the operator's per-stakes floor, raised one rung by substantial
complexity), what rung an agent runs at, and whether that agent may take the
work. One instance is built at boot and shared, so selection and dispatch cannot
reach different verdicts about the same pair.

An agent is a fixed `(role, personality, model)` unit, so a piece of work that
needs more capability goes to a **different agent**. The ladder is: the exact
rung the work demands, else the nearest rung above, else the nearest rung below
with the concession logged (`TASK_ASSIGNMENT_UNDER_CAPABILITY`). At or above the
configured park floor (`engine.capability_park_min_stakes`, default `high`) the
lower band is refused outright rather than conceded, because the inner-loop A/B
recording measured complex and epic briefs failing the correctness gate on a
basic model rather than degrading; parking for an operator decision is the
honest answer there.

Preferring an exact rung over a stronger one is also the standing org-wide cost
discipline: it selects the cheapest eligible rung on every assignment rather
than once a budget threshold is crossed. Below the park floor that includes a
rung under the requirement, taken with the concession logged rather than
treated as sufficient. Budget pressure never
re-points a binding; its hard stops refuse spend instead
(see [Budget](../design/budget.md)).

The whole ladder is operator-tunable and live: `engine.capability_floor_*`,
`engine.reasoning_effort_*`, `engine.red_team_min_stakes` and
`engine.capability_park_min_stakes` re-resolve through
`CapabilityPolicySettingsSubscriber` and take effect on the next judgement, with
no restart.

## Cognitive-load taxonomy

The capability judgement is grounded in what the prompt asks the model to do,
not in which subsystem the prompt lives in. Each purpose is assigned a
**kind**, and the kind determines the rung:

| Kind | Capability | What the prompt does |
|------|------------|----------------------|
| `classify_route_triage` | `basic` | Bounded-output classification, routing, triage, and connection probes. The answer space is small and the cost of a cheap model is low. |
| `judge_grade_verify` | `capable` | Evaluative judgements, grading, verification, consolidation, and run-time intervention proposals. Needs reliable reasoning but not open-ended generation. |
| `synthesise_generate_author` | `expert` | Open-ended synthesis, generation, authoring, code modification, and planning. Quality scales with capability, so the strongest rung is justified. |

### Measured: agent work is capability-bound before it is loop-bound

The inner-loop A/B recording ran the same five coding briefs on all three rungs
through both execution loops, 90 runs in all, and the rung separated the
outcomes far more sharply than the loop did. At **expert**, both loops graded
100 correctness on every brief. At **basic**, both fell below the correctness
gate on `loop-ab-pipeline` and `loop-ab-refactor`, and the complex and epic
buckets ended with no promotable loop at all, not because either loop is
unsuitable but because neither model could do the work.

The operational reading: a task whose complexity is `complex` or `epic` needs an
expert model. The loop is not entirely without effect at the margin: at
**capable**, openhands cleared the gate on `loop-ab-feature` and
`loop-ab-pipeline` where react was disqualified. But where both were
disqualified, at **basic**, no choice of loop recovered the brief.
Routing such work to a cheaper rung does not degrade gracefully; it fails the
acceptance checks outright. See
[the A/B harness](../design/loop-ab-harness.md) for the recording and its
limits.

## Pinned capabilities

Rungs per registered prompt purpose, grouped by rung.

### Basic (`example-basic-001`)

| Prompt class | Purpose |
|--------------|---------|
| `system:security:safety_classifier` | Classify whether content is safe before an agent acts on it. |
| `system:security:uncertainty` | Estimate model uncertainty for a security decision. |
| `system:memory:rerank` | Rerank retrieved memories for query relevance. |
| `system:memory:retrieval_route` | Route a retrieval query across the memory hierarchy. |
| `system:memory:retrieval_retry` | Reformulate and retry a failed memory retrieval. |
| `system:memory:fine_tune_query` | Generate a fine-tuning query for the embedding model. |
| `system:research:triage` | Triage a research brief into actionable directions. |
| `system:cos:routing` | Route a chief-of-staff request to a capability. |
| `system:intake` | Clarify an incoming request during intake. |
| `system:cos:turn_intent` | Classify an operator turn into a chief-of-staff intent. |
| `system:providers:test_connection` | Probe a provider connection with a minimal completion. |
| `system:providers:capability_classification` | Recommend a capability rung for a configured model from its metadata. |

### Capable (`example-capable-001`)

| Prompt class | Purpose |
|--------------|---------|
| `system:security:llm_evaluator` | Evaluate a security policy question with an LLM judge. |
| `system:vision_verify` | Verify a review artefact with a vision model. |
| `system:red_team:grounding` | Ground red-team probes against the target substrate. |
| `system:red_team:grounding_entailment` | Decide whether a claim is entailed by its cited source. |
| `system:classification:logical_contradiction` | Classify a run for self-contradictory reasoning. |
| `system:classification:numerical_drift` | Classify a run for numeric values drifting across turns. |
| `system:classification:context_omission` | Classify a run for context the agent failed to carry. |
| `system:classification:coordination_failure` | Classify a run for a breakdown between collaborating agents. |
| `system:memory:consolidate` | Consolidate raw memories into durable entries. |
| `system:memory:compress` | Compress memory artefacts to reclaim context budget. |
| `system:procedural:success_proposer` | Propose procedural memories from successful runs. |
| `system:procedural:propose` | Propose a procedural memory from a task trace. |
| `system:cos:chat` | Answer an operator question about the organisation. |
| `system:cos:narrative` | Narrate organisational state for the operator. |
| `system:cos:multi_voice` | Voice a chief-of-staff answer as the roles it draws on. |
| `system:plan_review:item_reply` | Reply to an operator comment on a plan item. |
| `system:steering:propose` | Propose a steering intervention for a running task. |
| `system:evolution:propose` | Propose an evolution to an agent's behaviour. |
| `system:workspace` | Answer a semantic query over a task workspace. |
| `system:verification` | Grade a deliverable against quality criteria. |
| `system:conflict:judge` | Judge which agent position wins a multi-agent conflict. |

### Expert (`example-expert-001`)

| Prompt class | Purpose |
|--------------|---------|
| `system:memory:abstractive` | Produce an abstractive summary of a memory set. |
| `system:knowledge:synthesis` | Synthesise a knowledge entry from source material. |
| `system:research:synthesis` | Synthesise research findings into a brief answer. |
| `system:research:planning` | Plan the steps to answer a research brief. |
| `system:cos:propose` | Propose an organisational change to the operator. |
| `system:charter:interview` | Interview the operator to draft an org charter. |
| `system:toolsmith:author` | Author a new tool definition for the toolsmith. |
| `system:meta:code_modification` | Modify code as part of a self-improvement strategy. |
| `system:client:requirement_generator` | Generate client requirements for a synthetic project. |

## Pin-validation benchmark

The policy is not advisory: `ModelPinValidationBenchmark`
(`synthorg.llm.pin_validation.benchmark`) exercises it. For each prompt
class it builds the canonical pin (the policy rung plus the deterministic
sampling parameters), runs a canonical probe against the pinned rung
through a deterministic provider, and grades **drift** by comparing a live
fingerprint, `sha256(model_id | temperature | top_p | max_tokens | output)`,
against a committed golden snapshot (`llm/pin_validation/golden.json`). The
sampling floats are serialised by their exact `float.hex()` representation
in the digest, so every distinct sampling value hashes differently and the
digest stays bit-reproducible across runs and platforms.

The probe runs offline against a scripted provider, so it costs nothing
and makes no provider call. It is a config-lint over the shipped pins,
not a measurement of a live model.

A mismatch (a capability reassignment, a sampling change, or a probe-pipeline
change) fails the grade until the golden is deliberately regenerated with
`scripts/refresh_model_pin_golden.py`. Because the golden is an
independent snapshot, the check is a genuine regression gate, not a
"pin checks the pin" tautology.

The provenance record is the committed golden plus its git history: the
snapshot names what every pin fingerprinted to, and the commit that
changed it names who changed it and when.

## Changing a pin

Reassigning a capability is a deliberate act:

1. Edit the entry in `synthorg.llm.model_capability_policy`.
2. Run `uv run python scripts/refresh_model_pin_golden.py` to regenerate
   the golden snapshot. The `pin-drift-regression` CI canary
   (`scripts/check_pin_golden_fresh.py`) fails until you do, so a
   capability or pin change cannot land without a fresh golden.
3. Commit both changes together.
