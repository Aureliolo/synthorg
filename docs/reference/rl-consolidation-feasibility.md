---
title: RL Consolidation Feasibility
description: "Feasibility analysis for reinforcement learning-based memory consolidation in SynthOrg: cost-benefit assessment, minimum viable design, break-even analysis, and decision rationale."
---

# RL Consolidation Feasibility

This page documents the feasibility analysis behind Decision D27 (see
[Decision Log](../architecture/decisions.md)).

---

## Problem Statement

Memory consolidation transforms raw episodic memories into compressed, generalised
representations that are cheaper to store and faster to retrieve. A reinforcement
learning (RL) consolidation policy would:

1. Observe an agent's memory store at a trigger point (session end, token threshold).
2. Decide which memories to merge, summarise, promote to semantic/procedural, or discard.
3. Receive a reward signal based on downstream retrieval quality and token cost.

The appeal is adaptability: an RL policy can learn per-agent consolidation patterns
without hand-crafted heuristics. The risks are reward design complexity, training
data requirements, and catastrophic failure modes.

---

## Cost-Benefit Assessment

| Dimension | LLM consolidation (current) | RL consolidation (proposed) |
|-----------|----------------------------|----------------------------|
| **Reward design** | N/A | Multi-objective, unsolved: readability + retrieval accuracy + synthesis fidelity + token cost. No consensus loss function. |
| **Ground truth** | None needed | ~1,000 annotated consolidation sessions minimum (Ouyang et al., RLHF scaling). Annotation cost: ~$15-30k at current rates. |
| **Training infra** | None | GPU cluster + replay buffer + policy checkpoint management. ~$8k-12k/month at current scale. |
| **Failure mode** | Graceful degradation (poor-quality summaries) | Silent data loss (RL drift deletes memories the reward function undervalues) |
| **Latency** | 200-800 ms per consolidation call | <10 ms per decision (policy inference only) after training |
| **Token cost** | 500-2,000 tokens per consolidation | 0 tokens per decision post-training; training cost front-loaded |
| **Improvement ceiling** | Bounded by LLM capability | Theoretically unbounded; empirically noisy at small scale |

**Verdict at 50-500 agent deployments**: training infra cost exceeds projected token
savings by approximately 12 months. RL becomes worth investigating at 10,000+ concurrent
agents where token costs at consolidation scale materially.

---

## Minimum Viable RL Design

If RL consolidation is revisited, the minimum viable design is:

### Architecture

- **Policy network**: 50M-parameter encoder (BERT-base equivalent) fine-tuned from a
  pretrained language model checkpoint. Input: memory entry text + metadata features
  (age, access count, category, tags). Output: action logits over
  {KEEP, MERGE, SUMMARISE, PROMOTE, DISCARD}.
- **Reward signal**: multi-component scalar with hand-tuned weights:

  ```text
  R = w1 * retrieval_accuracy_delta
    + w2 * (1 - token_compression_ratio)
    + w3 * synthesis_fidelity_score
    - w4 * discard_penalty_if_later_retrieved
  ```

  `discard_penalty_if_later_retrieved` is the most important term; it prevents
  the policy from aggressively discarding memories that are later queried.

- **Training algorithm**: Proximal Policy Optimisation (PPO) on rollouts collected
  from shadow deployments. Behavioural cloning on LLM consolidation decisions as
  warm start reduces cold-start instability.

### Data Pipeline

1. Run LLM consolidation in production (current approach) and log decisions.
2. Collect human preference labels on 1,000+ session pairs (preferred vs. rejected
   consolidation outputs).
3. Train reward model on preference data (Direct Preference Optimisation is the
   viable intermediate step; see D27).
4. Train RL policy against the reward model in shadow mode.
5. A/B test policy against LLM baseline before full rollout.

### Staged Rollout

| Stage | Traffic | Condition to advance |
|-------|---------|---------------------|
| Shadow | 0% live, 100% logged | Policy agreement with LLM baseline >85% |
| Canary | 5% live agents | Retrieval quality delta > 0 over 2 weeks |
| Partial | 25% live agents | Zero memory loss incidents in canary |
| Full | 100% live agents | Full monitoring suite in place |

Full rollout should be gated by the monitoring described in the next section.

---

## Required Monitoring (Pre-Deployment Gate)

RL consolidation **must not be deployed** without:

1. **Memory loss detection**: alert when a memory discarded by the policy is queried
   within 30 days. False-positive rate must be <0.1%.
2. **Distribution shift detector**: alert when policy input distribution drifts >2
   standard deviations from training distribution (covariate shift).
3. **Reward hacking detector**: monitor for consolidation patterns that maximise
   reward proxies without improving retrieval quality (e.g., keeping only
   high-frequency terms that inflate `retrieval_accuracy_delta`).
4. **Policy checkpoint rollback**: automated rollback to previous checkpoint if
   memory loss alert fires. No manual intervention required.

---

## Break-Even Analysis

Assumptions:

- Average consolidation token cost: 1,200 tokens per session
- LLM cost per 1M tokens: $3.00 (example-large-001 pricing at evaluation date)
- RL training infra: $10,000/month (A100 cluster + storage)
- RL policy inference: negligible ($0.001/month per agent)
- Annotation cost (one-time): $20,000

| Concurrent agents | Sessions/month | LLM token cost/month | RL savings/month | Months to break even |
|------------------|---------------|---------------------|-----------------|---------------------|
| 500 | 15,000 | $54 | ~$48 (90%) | >400 months |
| 5,000 | 150,000 | $540 | ~$486 (90%) | >42 months |
| 10,000 | 300,000 | $1,080 | ~$972 (90%) | ~31 months |
| 50,000 | 1,500,000 | $5,400 | ~$4,860 (90%) | ~6 months |

At 10,000 concurrent agents, break-even is approximately 31 months, still
unattractive given the annotation cost, training risk, and maintenance burden.
At 50,000+ agents, the economics become viable.

**Practical threshold**: revisit RL consolidation when the deployment base exceeds
**10,000 concurrent agents** AND token costs represent a material operational expense.

---

## Intermediate Step: DPO Fine-Tuning

Before full RL, Direct Preference Optimisation (DPO) fine-tuning of the LLM
consolidation policy is the recommended intermediate step:

1. Collect consolidation output pairs (LLM-generated).
2. Human-label preferences: which consolidation output is better?
3. Fine-tune the LLM on preference pairs using DPO loss.
4. Deploy fine-tuned model for consolidation calls only.

DPO requires no reward model, no RL training loop, and no policy rollback
infrastructure. It improves consolidation quality incrementally without the failure
modes of RL. Cost: ~$5,000-10,000 annotation + fine-tuning, one-time.

---

## Decision

**RL consolidation is not recommended for MVP.** Revisit at 10,000+ concurrent agent
deployments. If token costs become a concern before that threshold, apply DPO
fine-tuning as the intermediate step.

See [Decision Log D27](../architecture/decisions.md) for the decision record.
