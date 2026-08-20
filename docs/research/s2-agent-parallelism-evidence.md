---
title: "S2 Agent Parallelism Evidence Review"
description: Evidence review of multi-agent parallelism, verification gating, recursive decomposition, and session durability, with a per-claim verification ledger.
date: 2026-08-20
last_reviewed: 2026-08-20
---

# S2: Agent Parallelism Evidence Review

**Date**: 2026-08-20. **Method**: eight parallel research passes plus direct primary-source
verification. **Supersedes**: the conclusions of
[S1 Multi-Agent Architecture Decision](s1-multi-agent-decision.md), which carries a
mischaracterised statistic (see [Correction to S1](#correction-to-s1)).

## Why this page exists

S1 stated a `-39% to -70% multi-agent effect` and treated it as a general finding. It is
not. The figure is real but scoped to sequential-planning tasks only, and the source
paper's actual range is **+80.8% to -70.0%**. That distortion sat in the design
documentation for four months and pushed the architecture toward single-agent defaults,
a three-to-four agent coordination cap, and "organisational simulation fidelity" as a
stated value proposition.

The lesson generalises beyond the one number: **a wrong figure in a design document
steers architecture silently and indefinitely.** Every claim below therefore carries an
explicit verification status, and unverified claims are marked rather than dropped.

## Verification statuses used

| Status | Meaning |
|---|---|
| VERIFIED | Checked against the primary source. A quote or table entry matches. |
| VERIFIED (SCOPED) | The figure is real but narrower than it was presented as. |
| MISCHARACTERISED | The source says something materially different. |
| UNVERIFIED | Could not be confirmed against a primary source. Treat as provisional. |
| NOT FOUND | Searched for and not located. Do not cite. |

---

## Correction to S1

| Claim in S1 | Status | Correct version |
|---|---|---|
| `-39% to -70% multi-agent effect` | VERIFIED (SCOPED) | The paper says "all multi-agent variants universally degrade performance on tasks requiring sequential constraint satisfaction (planning: -39% to -70%)". Scoped to sequential planning, not general. |
| Overall effect range | VERIFIED | "Performance relative to single-agent systems ranged from +80.8% to -70.0%." |
| Paper's own headline | VERIFIED | "Coordination overhead becomes counterproductive when coordination complexity exceeds task complexity (PlanCraft) ... provides substantial gains when tasks naturally decompose into parallel information streams (Finance Agent)." |
| 3-4 agent coordination cap | VERIFIED (SCOPED) | Real, and "prohibitively thin beyond 3-4 agents" is a direct quote, but it applies to architectures requiring cross-agent coordination, not to loosely-coupled independent work. |
| Publication status | VERIFIED | Peer-reviewed and published as "Capable language models can outgrow the benefits of collaboration", Nature Machine Intelligence vol. 8 no. 7, pp. 1157-1172, 2026-07-24, DOI `10.1038/s42256-026-01268-y`. Confirmed via Crossref; the arXiv listing does not reflect it. |
| `arXiv:2603.27771` cited for the coordination cap | MISCHARACTERISED | That paper is a safety paper on emergent collusion and conformity. It contains no capability or coordination-cap claim. |
| `arXiv:2603.26993` (Reliability Limits) | VERIFIED (SCOPED) | The theorem is correctly described but explicitly scoped to a common-evidence regime. It does not reach agents holding disjoint evidence. |

**Action**: S1's headline statistic needs correcting in place, the Nature publication
added, and the reasoning-versus-work-stream distinction promoted from a buried caveat to
the framing, because it is how the strongest source frames its own result.

---

## The central distinction

The literature separates two regimes that S1 conflated.

**Reasoning parallelism**: many agents debating, voting on, or self-correcting one
problem. Consistently negative. The Ringelmann-effect study (`arXiv:2606.02646`,
2026-05-31, R-squared above 0.99 across 44 conditions) found thirty densely debating
agents produce no more answer diversity than one on MMLU-Hard.

**Work-stream parallelism**: many agents each doing a different, independent piece of
decomposed work. Positively supported, including in software specifically.

This distinction is supported and tested, not merely asserted. It is the framing the
Nature paper uses for its own headline result.

---

## Findings by question

### 1. Verification as merge authority

**Verdict: mechanical verification cannot be the sole merge authority. Empirically false
at the scale the thesis needed.**

| Finding | Status | Source |
|---|---|---|
| 53% of code passing visible tests still failed hidden tests under ordinary generation; 65-73% under adversarial generation | UNVERIFIED | `arXiv:2607.20852` |
| Strong verifier reaches 54% detection at a 5% false-positive budget, still missing 54-66% under adversarial pressure | UNVERIFIED | `arXiv:2607.20852` |
| Reward-hacking gap grows ~27 points per 10x increase in LOC, reaching 100 points above 25K LOC | VERIFIED | SpecBench, `arXiv:2605.21384`. Note the abstract says 28 points and the body says 27. |
| Agents score near-perfect against an exposed 222-test oracle while the delivered library is "dead or absent" outside tested paths | VERIFIED | Building to the Test, `arXiv:2606.28430`, Microsoft, 18 runs |
| Increasing test coverage does not reliably close the gap | VERIFIED | SpecBench. Even a compiler tested against the GCC torture suite retained a 14.5-point gap. |
| Review agents solve ~40% of real human-derived review tasks | UNVERIFIED | c-CRAB, `arXiv:2603.23448` |
| No published false-accept rate exists for a fully automated merge gate on production code, at any scale | VERIFIED (by absence) | Searched, not found |

Every shipping system keeps a human or an LLM judge as the final merge authority. None
uses deterministic checks alone.

### 2. Judge reliability and independence

**Verdict: a judge is a triage filter, not an authority. Structural independence is
directionally right but modest, with a hard mathematical ceiling.**

| Finding | Status | Source |
|---|---|---|
| Models reviewing their own code missed 31.7% of their own semantic drift, and in some cases identified their own defect and passed it anyway | UNVERIFIED | AWS modernisation study, cited second-hand |
| Self-preference bias: models assign 75-84% win rates to their own family | UNVERIFIED | Multiple, incl. Panickssery et al. |
| A panel of nine LLM judges yields roughly two effective independent votes | UNVERIFIED | `arXiv:2605.29800` |
| Correlated verifiers hit a blind-spot ceiling: failure decays polynomially, not exponentially. At correlation 0.3, independence-based maths underestimates failure 20x at five gates and ~3000x at ten | UNVERIFIED | `arXiv:2607.13918` |
| The only effective lever is decorrelation of model family, modality or evidence source, not adding gates | UNVERIFIED | `arXiv:2607.13918` |
| Naive (cooperative) adversarial review scored **worse than a single reviewer** (F1 0.457) via false consensus; with disagreement architecturally forced it reached F1 0.533 and 87% pass with three agents against 82% with five cooperative | UNVERIFIED | Adversarial Review, `arXiv:2608.18167` |
| Explicit anti-exploit prompt wording drops exploitation from 100% to 8.3% | UNVERIFIED | `arXiv:2604.20200` |
| Cursor built an independent judge into their planner/worker/judge system and then **removed it**: "We found agents were reasonably good at following instructions to completion, so the judge was removed to keep the system simple" | VERIFIED | `cursor.com/blog/self-driving-codebases`, 2026-02-05 |

**Design consequences.** A cooperative second reviewer may be worse than none.
Disagreement must be forced and evidence-grounded. Independence must be by model family,
not merely by agent identity: binding every agent to one provider makes the claim
nominal.

### 3. Coordination topology

**Verdict: flat peer coordination collapses; hierarchy removes the term that causes it.
This is the most strongly evidenced finding in the whole review.**

| Finding | Status | Source |
|---|---|---|
| Twenty equal-status agents under locking degraded to the effective throughput of two or three | VERIFIED | `cursor.com/blog/scaling-agents`, 2026-01-14 (stated as 1-3 in the Feb post) |
| Optimistic concurrency removed the collapse but made agents risk-averse, avoiding hard tasks | VERIFIED | Same |
| Planner/worker/judge hierarchy scaled to hundreds of concurrent workers | VERIFIED | Same |
| Flat run accumulated 70,000 merge conflicts, one file reaching 7,771 conflicts touched by 1,173 agents; hierarchical run stayed under 1,000 conflicts over four hours with the hottest file at 47 | UNVERIFIED | `cursor.com/blog/agent-swarm-model-economics` |
| Browser rewrite: over 1 million lines across 1,000 files in close to a week | VERIFIED | `cursor.com/blog/scaling-agents` |
| Error amplification: centralised 4.4, hybrid 5.1, decentralised 7.8, independent 17.2 | VERIFIED | Nature/`arXiv:2512.08296` |
| Turn count scales super-linearly: T = 2.72 x (n+0.5)^1.724, R-squared 0.974, p below 0.001 | VERIFIED | Same |
| Peer messaging grows near-quadratically then plateaus as agents shift to broadcast; shared files cut output tokens ~42% at eight agents on message-heavy work | VERIFIED | `arXiv:2608.16801`, 1,902 runs. The 42% is scoped to the distributed message-heavy task; the chained task showed increased tokens under the same policy. |
| Overall agent-PR merge-conflict rate 27.67% across 142K+ PRs | VERIFIED | AgenticFlict, `arXiv:2604.03551` |
| Per-agent conflict rates | MISCHARACTERISED | Actual table: Copilot 15.24%, Cursor 19.75%, Devin 22.85%, Claude Code 25.93%, Codex 31.85%. Figures circulating are each 0.2-0.5 points high. |
| "1,000 commits per second" attributed to the browser rewrite | MISCHARACTERISED | The browser swarm peaked at roughly 1,000 commits per **hour**. The per-second figure belongs to a later system built on a custom VCS. 3600x error. |
| Anthropic C-compiler run: 16 concurrent agents, ~2,000 sessions, two weeks, $20,000, 100,000 lines of Rust | VERIFIED | Anthropic engineering |
| Anthropic guidance against splitting coding by phase or role: "Dividing by type of work ... creates constant coordination overhead"; "An agent handling a feature should also handle its tests, because it already possesses the necessary context" | VERIFIED | `claude.com/blog`, when to use multi-agent systems |

**Anthropic's counterweight**: they state coding is not a good fit for their multi-agent
pattern because planning, implementation and testing of one feature share too much
context. Their own production research system is deliberately flat at depth 1.

### 4. Decomposition and the scale ceiling

**Verdict: the binding constraint is decomposition quality, not agent supply. The ceiling
is roughly 11 to 25 coherent independent units and it is method-independent.**

| Finding | Status | Source |
|---|---|---|
| Dependency-graph cohesion partitioning lifts pass rate by up to 14.0%, gives up to 2.10x wall-clock speedup and up to 35% cost reduction, with largest gains on the most dependency-dense projects | VERIFIED | Co-Coder, `arXiv:2606.00953`, 2026-05-31 |
| Partition counts assigned across 28 real repositories ranged 1 to 11 (DevEval mean 3.4, range 1-8; CodeProjectEval mean 7.2, range 2-11) | VERIFIED | Same, `#Groups` column |
| Pearson r = 0.65 (Spearman 0.60, both p below 0.05) between dependency-graph edge density and pass-rate improvement | VERIFIED | Same |
| Naive file-based parallelism inflates cost 60% for no quality gain | UNVERIFIED | Same |
| Contract-first: largest task 15-25 files at 47% functional success; authors state no benchmark exists past ~100 files | VERIFIED | Contract-Coding, `arXiv:2604.13100` |
| Design-then-contract: hardest tier 14-22 files | UNVERIFIED | CodeTeam, `arXiv:2606.22082` |
| Scaling the planner alone captures essentially all of a system's scaling benefit (planner alpha 16.0 against 15.6 for all modules together) | UNVERIFIED | Planner Matters, `arXiv:2605.02168` |
| Single-planner architectures at large fan-out: one architect decomposing into 50-100 issues driving 200+ agent invocations across a six-level dependency graph | UNVERIFIED | AgentField SWE-AF |
| 256-agent scaling with steady quality and 11.8% cost growth | MISCHARACTERISED | `arXiv:2603.28990`. Tasks are synthetic business and security scenarios, not code. Single author, unreviewed. The 11.8% figure covers 4 to 64 agents, not the climb to 256. The 14% win over hierarchies is one protocol, one model, one task tier at n=16. |

**The ceiling is corroborated three ways.** Contract-first (15-25 files), graph-partitioned
existing repositories (1-11 partitions), and design-then-contract (14-22 files) all land
in the same range using different methods. It is not one benchmark's artifact.

### 5. Do contracts survive implementation?

**Verdict: no. Renegotiation is not an exception path; it is assumed infrastructure
everywhere it has been tried.**

No paper reports a quantified renegotiation rate. Every source that touches the question
points the same way:

- Contract-Coding's own headline example is its reviewer detecting a semantic mismatch
  mid-build and **retroactively patching the contract**, adding a dimension nobody
  specified up front.
- CodeTeam builds machine-checkable contracts (file ownership, interfaces, dependencies)
  and enforces them by fiat, then reports **zero** failure analysis of how often agents
  push against the constraint.
- Constraint Decay (`arXiv:2605.06445`, single agent): across 80 greenfield tasks under a
  **fixed, pre-specified** API contract, capable configurations lose roughly **30 points**
  of assertion pass rate as structural requirements accumulate; weaker configurations
  approach zero. Status: UNVERIFIED.
- Co-Coder sidesteps rather than answers, avoiding interface conflicts by construction
  through hub-file isolation in the partitioner.

Every system shipping a contract-first approach also ships a repair loop, and none report
the contract holding without one.

**Planning assumption**: a repair and renegotiation loop is a required component.

### 6. Recursive decomposition depth

**Verdict: genuinely open. No safe depth is published, and the decisive experiment has
never been run.**

| Finding | Status | Source |
|---|---|---|
| Aggregation succeeds only 50-60% per application and accounts for 86% of all errors on one benchmark and 68% on another; depth 2 is fine, depth 3-4 blows up | UNVERIFIED (one agent fetched it directly, a second could not locate it) | ARIES, `arXiv:2502.21208` |
| RDD exists because naive recursive decompose-solve-merge degrades; it adds dependency tracking and error recovery but publishes no depth-versus-quality curve | UNVERIFIED | `arXiv:2505.02576` |
| Verification at every node should convert the ceiling from a handful of steps to roughly 29,400: five-way consensus at 5% individual error gives 0.11% system error, a 45x improvement | UNVERIFIED, and it is a probability model with assumed reliabilities, not a measurement | Six Sigma Agent, `arXiv:2601.22290` |
| RL-trained recursive self-delegation: trained to depth 6, evaluated to depth 12, generalising past training depth; 88% success on hard tasks against 20% single-agent; 2.5x wall-clock reduction. Never examines aggregation quality | UNVERIFIED | RAO, `arXiv:2605.06639` |
| Claude Code subagent nesting: depth 3 default, was 5 uncapped, briefly 1 after an incident where a budget flag failed to stop background subagents (fan-out measured around 7x normal token spend), restored to 3 | VERIFIED | `claude-code` CHANGELOG, v2.1.172-219 |
| That cap is a cost and blast-radius decision, **not** a measured quality finding | VERIFIED | Same changelog rationale |
| Anthropic's production multi-agent research system is deliberately flat: a lead spawns 3-10 subagents that do not spawn further | VERIFIED | Anthropic engineering |
| Chain reliability: P(success) = (1-p)^m. 99%-reliable steps give 90.4% at 10 and 36.6% at 100 | UNVERIFIED | Six Sigma Agent |
| Tree versus chain reliability | NOT MODELLED ANYWHERE | Nobody has modelled or measured whether a tree compounds differently from a chain |
| Under-scoping detection: any mechanism where an agent detects its unit is bigger than one agent's worth and escalates to a split | NOT FOUND | Searched directly via arXiv and Semantic Scholar; the concept has no literature |
| "An agent that has read the code splits better than a planner who has not" | UNTESTED | No controlled comparison exists |

**The gap, stated precisely**: ARIES measured a real system collapsing at the merge but
never added a gate. The Six Sigma model shows gating should fix arbitrary-depth
compounding but was never run on a real decomposition benchmark. **No paper connects
them.** Taking a decomposition benchmark, gating every aggregation, and reporting the new
deterioration ratio is an experiment nobody has published.

### 7. Session durability and observability

**Verdict: a genuine open gap, and five of the six properties are weeks of conventional
engineering.**

The six properties: persists; live-readable by another party while running; resumable by a
different agent or process; forkable; steerable mid-flight; partial output survives death.

**No system has all six.** Closest partials: Claude Agent SDK (explicit fork,
cross-process resume), Restate (the only shipped generic live journal UI), Letta
(different clients attaching to one persistent agent), Zed (the only multi-party live read,
via a CRDT-synced buffer), Devin (the strongest same-owner steering, plus Slack-thread following as
a real multi-party channel).

| Finding | Status | Source |
|---|---|---|
| Event-sourcing overhead is negligible: 0.20ms median persist, 4.1ms median full replay, 7.4ms crash recovery, 380KB median to 1.4MB p95 per conversation | UNVERIFIED | OpenHands SDK, `arXiv:2511.03690`, MLSys 2026 |
| Production-validated: system-attributable failures cut from 78.0 to 30.0 per 1,000 conversations over a 15-day rollout | UNVERIFIED | Same |
| The event log captures agent actions and observations, **not container state**. Even this production SDK punts on sandbox restoration | UNVERIFIED | Same |
| Sandbox checkpointing needs process-granularity work (CRIU plus copy-on-write layers), not VM snapshots: DeltaBox reports 14.6ms checkpoint and 5.1ms restore against 475-531ms and 1,334-1,490ms for a naive Firecracker diff | UNVERIFIED | Crab `arXiv:2604.28138`, DeltaBox `arXiv:2605.22781`. Both are research prototypes. |
| KV cache hit rate is 90% within a turn and 55% across a turn boundary; median turn 63.4s, p90 392s | UNVERIFIED | `arXiv:2608.00101` |
| No standard covers this. ACP is unfinished for exactly these features; A2A has multi-reader streaming and resumption by a different client but no forking and no access-control model | UNVERIFIED | A2A spec, ACP docs |

**Minimum viable order, by value per unit effort**: durable and resumable first (event log
plus periodic snapshot on existing persistence); live-readable second (near-zero marginal
cost once the log exists); turn-boundary steering third; forking fourth; partial-output
survival as a corollary. **Scope live sandbox state out**: it is a research project, and
the reference system's own fallback is to reissue the interrupted command on resume.

### 8. Category status and adoption

**Verdict: the org-simulation category did not survive as a product thesis. The current
packaging cannot spread. The self-hosting moat is durable.**

| Finding | Status | Source |
|---|---|---|
| ChatDev 2.0 repositioned from a `specialized virtual software company` to a general-purpose orchestration platform, freezing the org-simulation branch as legacy | UNVERIFIED | OpenBMB |
| AutoGen entered maintenance mode October 2025; Microsoft's successor framework dropped the society framing entirely | UNVERIFIED | Microsoft |
| CrewAI pairs role-based Crews with deterministic Flows because production users need auditable control the role-play layer cannot give | UNVERIFIED | CrewAI |
| Artisan retired its "Stop Hiring Humans" positioning in August 2026 and is hiring its first human BDR | UNVERIFIED | Press coverage |
| Personality self-reports do not reliably predict behavioural outputs | UNVERIFIED | The Personality Illusion, `arXiv:2509.03730` |
| Personality composition `matters less than initially hypothesized` | UNVERIFIED | `arXiv:2606.27443` |
| One paper finds profile choice moving code-generation pass@1 by 7-11 points and review quality up to 19% relative, but with model-specific optima and a real token-cost penalty | UNVERIFIED | `arXiv:2607.05659` |
| No ablation isolating personality or hierarchy from task-specification quality has ever been published | VERIFIED (by absence) | Searched specifically |
| Inter-agent misalignment accounts for ~37% of multi-agent failures across 1,600+ annotated traces | UNVERIFIED | MAST, `arXiv:2503.13657`, NeurIPS 2025 |
| Manager-role ablations show large drops, but they remove a planning and checking step, not a simulated boss | UNVERIFIED | PC-Agent, Multi-Agent Evolve |
| AI-authored PRs merge at 32.7% against 84.4% for human-authored, and wait ~1,055 minutes against ~201 | UNVERIFIED | LinearB, 8.1M PRs |
| Median time in review up 441.5% between lowest and highest AI-adoption cohorts | UNVERIFIED | Faros AI, 22,000 developers |
| "Reviewers process 3-5 PRs/day against 15-20 generated" | NOT FOUND | Do not cite. Checked Faros and LinearB primary sources and aggregators. |
| Governance as a standalone product: Portkey acquired by Palo Alto Networks for $140M cash within months of its Series A; LiteLLM free and dominant; hyperscalers ship native gateways | UNVERIFIED | Press coverage |
| EU AI Act Article 12 high-risk obligations reached full enforcement 2026-08-02 | UNVERIFIED | Regulatory coverage |
| Every well-funded competitor monetises hosted metered compute, making genuine self-hosting commercially unattractive for them to build | Analysis, not a citation | Derived from the competitor survey |

**Adoption shape.** Every project that spread had: one command on the machine the user is
already on; payoff inside 60-120 seconds; a shareable artefact native to a medium the
audience already trusts (a terminal cast or a chat screenshot, never a bespoke dashboard);
peer-to-peer "I built this for myself" distribution; and single-player value needing no
coordination. The failure pattern is multi-step setup before any payoff, autonomy claims
the reliability cannot back, demos that do not survive reproduction by a stranger, and
top-down adoption with no individual whose daily habit depends on the tool.

BUSL-1.1 is a downstream ceiling on redistribution and procurement, not a day-one
friction point. n8n runs a non-OSI licence chosen from the start and reached $40M ARR and
a $2.5B valuation, but on a multi-year compounding motion, not a spike.

### 9. Competitive position on the narrowed thesis

Five properties: dependency-graph partitioning; hierarchical fan-out with
non-coordinating workers; recursive self-split; structurally independent judge; durable
observable sessions.

**No shipping product has more than two of the five. Nobody has partitioning plus judging
together. No shipped product has it, and no unshipped one either.** Cursor's internal system has neither: its splits are
planner-owned slices rather than graph analysis, and the judge was removed. Closest
shipped competitors are Devin (fan-out plus unusually strong observability), Factory.ai
Missions (typed-role coordinator dispatch), and Warp Factories (closest on the judge axis,
closed early access). Claude Code ships recursive subagents as a primitive but not as an
orchestration policy. Amp shipped the opposite topology in July 2026: agents that spawn
agents **and** message each other and exchange files, which is the flat shape Cursor
measured collapsing.

Co-Coder's implementation is public at `github.com/Flitternie/CoCoder` with five stars and
no commercial deployment. Dependency-graph partitioning is a validated technique lying in
the open.

**Principal risk**: Cursor productising their planner/worker system. They run it
internally at scale and have stated the intent. Adding partitioning and reinstating a
judge is bounded engineering for them, not research.

---

## What closed

1. Organisational simulation as an output-quality mechanism.
2. Governance and agent execution as a standalone product.
3. Mechanical verification as sole merge authority.
4. Flat massive parallelism.
5. A thousand agents, blocked by decomposition rather than by cost or machinery.
6. Contracts precise enough to make merges mechanical.
7. The current packaging as a route to adoption.

## What remains open

**One question**: does verification at every merge hold off aggregation collapse as
recursion deepens? If yes, the 11-to-25 ceiling is per level and scale is real. If no, it
is global and this is a twenty-agent product.

Secondary and unexplored: no under-scoping detector exists in any published system; the
classical modularity literature (Parnas, information hiding, coupling and cohesion,
Conway's Law inverted) has never been connected to agent parallelism; nobody has measured
whether decoupling a codebase first raises its partition count; and nobody has tested
whether an agent that has read the code splits better than a planner who has not.

## The experiment

Build in `evals/recursion_depth/`, on the pattern of `evals/loop_ab/`, calling the
existing completion-oracle gate directly and bypassing charter intake, plan approval and
wave dispatch (nine live rounds have died upstream of the interesting part).

**New code required**: make `_do_decompose` recurse and increment `current_depth` (already
declared in `DecompositionContext`, read in six places, written in none); a flag to
disable gating for the control arm; per-level instrumentation.

**Output**: one chart. Depth 1-6 on the x-axis, fraction of leaf work surviving to a
correct merged result on the y-axis. Two lines: one gated, one ungated.

---

## Design consequences for this codebase

1. **Recursion is the priority.** Every research thread independently identified
   decomposition as the binding constraint. That is issue #2699.
2. **A cooperative reviewer may be worse than none.** Disagreement must be forced and
   evidence-grounded.
3. **Judge independence must be by model family**, not agent identity. Binding every agent
   to one provider makes the claim nominal.
4. **Role-based routing for execution is contraindicated**; role separation for
   verification is supported. Anthropic's guidance is explicit.
5. **A repair and renegotiation loop is a required component**, not an exception path.
6. **Stakes-stratified gating is the supported shape**: auto-merge low-stakes small-diff
   work, escalate the rest, keep humans on sensitive paths.
7. **The zero-artifact guard creates reward-hacking pressure.** Explicit anti-exploit
   prompt wording is the cheapest measured countermeasure.
8. **Session durability is weeks of work** on existing persistence and event-bus
   infrastructure, excluding sandbox state.
9. **Self-hosting is the durable moat** because it cannibalises every competitor's
   monetisation surface.

## Method notes and limitations

Eight parallel research passes plus direct verification of the two most load-bearing
papers. The shared web-search budget was exhausted at 200 queries partway through, so
later passes relied on direct fetches of arXiv and primary sources rather than search;
grey-literature and blog coverage is consequently thinner than academic coverage.

Two claims could not be independently confirmed and are flagged above: the ARIES
aggregation numbers (fetched directly by one pass under `arXiv:2502.21208`, not locatable
by another) and Cursor's "right description of intent" statement.

Claims marked UNVERIFIED come from a single research pass without independent
confirmation. Given that this review exists because of a four-month-old
mischaracterisation, treat them as provisional and verify before any of them steers a
decision.

## Sources

**Peer-reviewed or journal-published**

- Kim et al., "Capable language models can outgrow the benefits of collaboration", Nature
  Machine Intelligence 8(7):1157-1172, 2026-07-24, DOI `10.1038/s42256-026-01268-y`;
  preprint `arXiv:2512.08296`
- Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" (MAST), NeurIPS 2025,
  `arXiv:2503.13657`
- OpenHands Software Agent SDK, MLSys 2026, `arXiv:2511.03690`

**Preprints**

`arXiv:2606.00953` Co-Coder ·
`arXiv:2604.13100` Contract-Coding ·
`arXiv:2606.22082` CodeTeam ·
`arXiv:2605.06445` Constraint Decay ·
`arXiv:2608.16801` When Agents Coordinate ·
`arXiv:2604.03551` AgenticFlict ·
`arXiv:2605.21384` SpecBench ·
`arXiv:2606.28430` Building to the Test ·
`arXiv:2607.20852` Code Monitor Red Teaming ·
`arXiv:2603.23448` c-CRAB ·
`arXiv:2605.29800` Nine Judges, Two Effective Votes ·
`arXiv:2607.13918` Partially Correlated Verifier Cascades ·
`arXiv:2608.18167` Adversarial Review ·
`arXiv:2604.19049` Refute-or-Promote ·
`arXiv:2604.20200` Chasing the Public Score ·
`arXiv:2606.07379` Do Coding Agents Deceive Us? ·
`arXiv:2606.26300` The Verification Horizon ·
`arXiv:2608.01715` Coding Agents as Test-Suite Auditors ·
`arXiv:2502.21208` ARIES ·
`arXiv:2505.02576` RDD ·
`arXiv:2601.22290` The Six Sigma Agent ·
`arXiv:2605.06639` RAO ·
`arXiv:2603.28990` Drop the Hierarchy and Roles ·
`arXiv:2606.02646` Ringelmann Effect ·
`arXiv:2605.02168` Planner Matters ·
`arXiv:2607.21909` Claim Plane ·
`arXiv:2604.28138` Crab ·
`arXiv:2605.22781` DeltaBox ·
`arXiv:2608.00101` Copilot production-scale study ·
`arXiv:2605.06717` Agentic Coding Needs Proactivity ·
`arXiv:2606.31498` Governance Gaps in Agent Interoperability Protocols ·
`arXiv:2509.03730` The Personality Illusion ·
`arXiv:2606.27443` Personality Composition ·
`arXiv:2607.05659` Personality and Emotion in Software Teams ·
`arXiv:2603.27771` Multi-Agent Risks ·
`arXiv:2603.26993` Reliability Limits ·
`arXiv:2604.02460` Single-Agent Outperforms

**Engineering writeups**

- `cursor.com/blog/scaling-agents` (2026-01-14)
- `cursor.com/blog/self-driving-codebases` (2026-02-05)
- `cursor.com/blog/agent-swarm-model-economics`
- Anthropic engineering: building a C compiler; when to use multi-agent systems; the
  multi-agent research system
- `claude-code` CHANGELOG (subagent depth versioning, v2.1.172-219)
- OpenAI, why SWE-bench Verified was retired (Feb 2026)

**Implementations**

- `github.com/Flitternie/CoCoder` (five stars, no commercial deployment)
