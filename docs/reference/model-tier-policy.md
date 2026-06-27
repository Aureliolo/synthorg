---
title: Model Tier Policy
description: The purpose-to-tier convention that pins each prompt class to a design tier, and how the pin-validation benchmark consumes it.
---

# Model Tier Policy

A model pin records a **design tier**, not a vendor model. SynthOrg is
provider-agnostic: no canonical vendor model is privileged, so a prompt
class pins one of the vendor-agnostic archetype tiers
(`example-large-001`, `example-medium-001`, `example-small-001`) that
[`heuristic_tier`](../../src/synthorg/budget/model_tier.py) resolves. This
page documents which tier each system prompt class is pinned to and the
reasoning behind it.

The policy lives in `synthorg.llm.model_tier_policy`. It maps every
`PromptPurposeId` in the [prompt-purpose registry](../design/evaluation-loop.md)
to a tier, with an import-time guard that rejects any purpose missing an
entry. The [pin-validation benchmark](#pin-validation-benchmark) consumes
the policy to validate each prompt class against its pinned tier, and the
per-class `ModelPinMetadata` rollout assigns its tiers from it.

## Cognitive-load taxonomy

The tier judgement is grounded in what the prompt asks the model to do,
not in which subsystem the prompt lives in. Each purpose is assigned a
**kind**, and the kind determines the tier:

| Kind | Tier | What the prompt does |
|------|------|----------------------|
| `classify_route_triage` | `small` | Bounded-output classification, routing, triage, and connection probes. The answer space is small and the cost of a cheap model is low. |
| `judge_grade_verify` | `medium` | Evaluative judgements, grading, verification, consolidation, and run-time intervention proposals. Needs reliable reasoning but not open-ended generation. |
| `synthesise_generate_author` | `large` | Open-ended synthesis, generation, authoring, code modification, and planning. Quality scales with capability, so the strongest tier is justified. |

## Pinned tiers

Tiers per registered prompt purpose, grouped by tier.

### Small (`example-small-001`)

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
| `system:hr:calibration` | Sample calibration judgements for performance scoring. |
| `system:providers:test_connection` | Probe a provider connection with a minimal completion. |

### Medium (`example-medium-001`)

| Prompt class | Purpose |
|--------------|---------|
| `system:security:llm_evaluator` | Evaluate a security policy question with an LLM judge. |
| `system:vision_verify` | Verify a review artefact with a vision model. |
| `system:red_team:grounding` | Ground red-team probes against the target substrate. |
| `system:memory:consolidate` | Consolidate raw memories into durable entries. |
| `system:memory:compress` | Compress memory artefacts to reclaim context budget. |
| `system:procedural:success_proposer` | Propose procedural memories from successful runs. |
| `system:procedural:propose` | Propose a procedural memory from a task trace. |
| `system:cos:chat` | Answer an operator question about the organisation. |
| `system:cos:narrative` | Narrate organisational state for the operator. |
| `system:steering:propose` | Propose a steering intervention for a running task. |
| `system:evolution:propose` | Propose an evolution to an agent's behaviour. |
| `system:workspace` | Answer a semantic query over a task workspace. |
| `system:verification` | Grade a deliverable against quality criteria. |

### Large (`example-large-001`)

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
| `system:hr:training_curation` | Curate training examples from agent transcripts. |

## Pin-validation benchmark

The policy is not advisory: the `model-pin-validation`
[`ExternalBenchmark`](../design/evaluation-loop.md#external-benchmarks)
exercises it on every eval cycle. For each prompt class it builds the
canonical pin (the policy tier plus the deterministic sampling
parameters), runs a canonical probe against the pinned tier through a
deterministic provider, and grades **drift** by comparing a live
fingerprint, `sha256(model_id | temperature | top_p | max_tokens | output)`,
against a committed golden snapshot (`pin_golden.json`).

A mismatch (a tier reassignment, a sampling change, or a probe-pipeline
change) fails the grade until the golden is deliberately regenerated with
`scripts/refresh_model_pin_golden.py`. Because the golden is an
independent snapshot, the check is a genuine regression gate, not a
"pin checks the pin" tautology.

On a clean grade the benchmark stamps `validated_at` for the prompt class
through the `ModelPinValidationRepository`, so
`ModelPinMetadata.model_version_pinned_at` means "last validated against
its tier", not "the day we wrote it". The stamp is best-effort: a
persistence failure is logged but never flips a clean drift verdict.

## Changing a pin

Reassigning a tier is a deliberate act:

1. Edit the entry in `synthorg.llm.model_tier_policy`.
2. Run `uv run python scripts/refresh_model_pin_golden.py` to regenerate
   the golden snapshot (the benchmark fails until you do).
3. Commit both changes together. The next eval cycle re-validates the pin
   and refreshes its `validated_at`.
