# Research and Prior Art

The external evidence the design rests on, and where to find the two internal reviews that
weigh it. Read this page to find out which claim is current and how strongly it is
supported; read the reviews themselves before citing any figure.

## The two evidence reviews

Two internal reviews hold the primary-source work. They are not equal in standing.

| Review | Date | Standing |
| --- | --- | --- |
| [S2 Agent Parallelism Evidence Review](../research/s2-agent-parallelism-evidence.md) | 2026-08-20 | Current. Eight parallel research passes plus direct primary-source verification, with a per-claim verification status. |
| [S1 Multi-Agent Architecture Decision](../research/s1-multi-agent-decision.md) | 2026-04-11 | Superseded in part. Two of its claims are corrected by S2; the rest of the page stands. |

S2 exists because a mischaracterised figure in S1 steered the architecture for four months.
Every claim in S2 therefore carries an explicit status: VERIFIED, VERIFIED (SCOPED),
MISCHARACTERISED, UNVERIFIED, or NOT FOUND. **Check the status column before citing
anything from it.** UNVERIFIED means a single research pass with no independent
confirmation, and a large share of the competitive and adoption findings sit there.

The two S1 claims S2 corrects:

- The `-39% to -70%` multi-agent effect is scoped to sequential-planning tasks. The source
  paper's overall range is `+80.8%` to `-70.0%`, and its own conclusion is that fit
  between coordination shape and task structure decides the outcome.
- `arXiv:2603.27771` is a safety paper on emergent collusion and conformity. It carries no
  capability or coordination-cap claim and must not be cited for either.

## What the reviews closed

S2 records seven positions as closed. Anything on this page or elsewhere in the docs that
depends on one of them is wrong:

1. Organisational simulation as an output-quality mechanism.
2. Governance and agent execution as a standalone product.
3. Mechanical verification as sole merge authority.
4. Flat massive parallelism.
5. A thousand agents. The block is decomposition rather than cost or machinery.
6. Contracts precise enough to make merges mechanical.
7. The current packaging as a route to adoption.

Closing (1) and (3) is what removed organisational realism and automated verification from
the set of things worth claiming. Roles, staffing, and the approval gate remain shipped
machinery; what closed is the argument that simulating an organisation is why output is
good.

## What remains open

One question, and it is the one being measured: **does verification at every merge hold off
aggregation collapse as recursion deepens?** If it does, the decomposition ceiling is per
level and depth buys scale. If it does not, the ceiling is global. Nobody has published an
answer and this project does not have one either, so no page may state a size of build the
system handles.

Secondary gaps S2 found unexplored anywhere: no under-scoping detector exists in any
published system; the classical modularity literature has never been connected to agent
parallelism; nobody has measured whether decoupling a codebase first raises its partition
count; and nobody has tested whether an agent that has read the code splits better than a
planner that has not.

## The findings the design rests on

### Decomposition quality is the binding constraint

Not agent supply, and not coordination cost. S2 corroborates a ceiling of roughly 11 to 25
coherent independent units three separate ways: contract-first decomposition (15 to 25
files at 47% functional success, Contract-Coding `arXiv:2604.13100`), dependency-graph
partitioning of real repositories (1 to 11 partitions across 28 projects, Co-Coder
`arXiv:2606.00953`), and design-then-contract (14 to 22 files, CodeTeam
`arXiv:2606.22082`). Three methods, one range, so it is not one benchmark's artefact.

This is a finding about published systems. Whether it applies per level of a tree or
globally is the open question above.

### Reasoning parallelism and work-stream parallelism are different regimes

Many agents debating one problem is consistently negative in the literature. Many agents
each building a different independent piece of decomposed work is positively supported,
including on software specifically. This distinction is how the strongest source frames
its own headline result, and it is why the fan-out here dispatches independent units
rather than convening a panel.

### Hierarchy removes the flat-topology collapse

Flat peer coordination, where every agent can message every other, degrades as it widens.
Hierarchical fan-out with non-coordinating workers does not carry the term that causes
that collapse.

### An independent judge is a triage filter, not an authority

This caps what may be claimed about review, and the cap matters more than the finding.
Self-review misses a substantial share of a model's own semantic drift; models prefer
their own family's output; a panel of nine judges yields roughly two effective independent
votes; and correlated verifiers hit a blind-spot ceiling where failure decays
polynomially rather than exponentially, so adding gates buys much less than the
independence maths suggests. The only effective lever S2 found is **decorrelation by model
family, modality or evidence source**, not more gates. Cooperative second review can score
worse than no second review through false consensus, so disagreement has to be forced and
evidence-grounded.

One consequence is implemented rather than asserted: the reviewer is structurally
prevented from being the author. It does not make the output correct, and no page may say
otherwise. The other, binding review to a different model family, remains a
recommendation: reviewer selection weighs capability, not lineage, so two agents can share
a family and therefore a blind spot.

### Contracts do not survive implementation

Renegotiation is not an exception path, it is assumed infrastructure. A repair loop is a
required component rather than a refinement.

### Self-hosting is durable rather than clever

Every well-funded competitor monetises hosted metered compute, so a genuinely
self-hostable equivalent cannibalises their margin and is unattractive for them to build.
S2 records this as analysis derived from its competitor survey, not as a citation.

## Agent scaling

[Kim et al., "Towards a Science of Scaling Agent Systems"](https://arxiv.org/abs/2512.08296)
evaluated 260 configurations spanning six agentic benchmarks, five canonical topologies
(single-agent plus independent, centralised, decentralised and hybrid multi-agent) and
three language-model families. Peer-reviewed and published as "Capable language models can
outgrow the benefits of collaboration", Nature Machine Intelligence vol. 8 no. 7,
pp. 1157-1172, 2026-07-24, DOI `10.1038/s42256-026-01268-y`; the arXiv listing does not
reflect that.
<!-- lint-allow: doc-numeric-macros -- third-party paper's own experiment counts and citation, not a build-time stat -->

What it supports, and where each finding landed:

- **Relative performance against a single-agent baseline ranges from `+80.8%` on
  decomposable financial reasoning to `-70.0%` on sequential planning.** The degradation
  end is real and is what the routing decision turns on; quoting it without the positive
  end misrepresents the paper. Read alongside
  [S2's correction table](../research/s2-agent-parallelism-evidence.md#correction-to-s1).
- **Task decomposability is the primary predictor** of multi-agent success. This is the
  finding the decomposition subsystem exists to act on.
- **A coordination-metrics suite** (efficiency, overhead, error amplification, message
  density, redundancy) explains a majority of performance variance. Implemented in
  `budget/coordination_metrics.py` and its collector.
- **Coordination overhead has a band**, above which it is counterproductive. Informs how
  the orchestration ratio metric is read.
- **An error taxonomy** (logical contradiction, numerical drift, context omission,
  coordination failure) with architecture-specific patterns. Carried as opt-in
  classification in the coordination pipeline.
- **Topology can be selected from measurable task properties.** Implemented in
  `engine/routing/topology_selector.py`.
- **Centralised verification contains error amplification** far more than independent
  agents do.

!!! note "Applicability"

    The paper tested identical agents on individual tasks. This system dispatches
    role-differentiated agents against a decomposed tree. Its thresholds, including the
    capability ceiling and the small-team sweet spot, are directional here and have not
    been validated in this context. The `3-4 agent` cap in particular is scoped to
    architectures requiring cross-agent coordination and does not reach loosely-coupled
    independent work. <!-- lint-allow: doc-numeric-macros -- directional research-paper thresholds, not build-time stats -->

## Why this is built rather than forked

Built from scratch on top of libraries, not forked from an existing framework.

The distinguishing properties are recursive decomposition into a tree of units that can be
built independently, a structurally independent check on each part, self-hosted execution
against any provider, and a durable observable session. S2's competitor survey found no
shipping product combining more than two of the five properties it tested for, and none
combining dependency-graph partitioning with an independent judge. That survey is a point
observation of 2026-08-20 over the products it names, not an enumeration of the market,
and it does not reach unshipped systems.

Forking a role-based or graph-based framework would mean fighting its execution model to
add recursive splitting, per-part isolation and a reviewer structurally barred from being
the author. None of that is a small addition to an existing loop.

For a feature-by-feature comparison against other frameworks, see
[Framework Comparison](comparison.md).

### Libraries leveraged

| Library | Role |
|---------|------|
| **LiteLLM** | Provider abstraction (<!--RS:providers_via_litellm-->95+<!--/RS--> providers, unified API) |
| **pgvector / sqlite-vec** | Vector search for agent memory, inside the operational database |
| **Litestar** | API layer (see [Tech Stack](../architecture/tech-stack.md#why-litestar-over-fastapi) for rationale) |
| **MCP** | Tool integration standard |
| **Pydantic** | Config validation and data models |
| **React 19** | Web dashboard framework (see [Tech Stack](../architecture/tech-stack.md)) |

## Sources

### Internal reviews

- [S2 Agent Parallelism Evidence Review](../research/s2-agent-parallelism-evidence.md):
  current; per-claim verification ledger.
- [S1 Multi-Agent Architecture Decision](../research/s1-multi-agent-decision.md):
  superseded in part by S2.
- [Multi-Agent Failure Audit](../research/multi-agent-failure-audit.md): failure-pattern
  guardrails.
- [Embedding Evaluation](embedding-evaluation.md): how memory embedding models are chosen.

### Papers

- [Kim et al., "Towards a Science of Scaling Agent Systems"](https://arxiv.org/abs/2512.08296):
  agent scaling. Published as "Capable language models can outgrow the benefits of
  collaboration", Nature Machine Intelligence, 2026-07-24.
- [Cemri et al., "Why Do Multi-Agent LLM Systems Fail?"](https://arxiv.org/abs/2503.13657):
  the Multi-Agent System Failure Taxonomy (MAST), 14 failure modes in three categories,
  from over 1,600 annotated traces across seven frameworks. Basis of the coordination
  error classification.
  <!-- lint-allow: doc-numeric-macros -- third-party paper's own dataset counts, not a build-time stat -->
- [Gloaguen et al., "Evaluating AGENTS.md"](https://arxiv.org/abs/2602.11988): repository
  context files do not generally improve task success rates and raise inference cost by
  over 20% on average; instructions are followed well, repository overviews are not
  helpful. Basis of the non-inferable-only principle for system prompts.
- [Zhao et al., "LMEB: Long-horizon Memory Embedding Benchmark"](https://arxiv.org/abs/2603.12572):
  22 datasets, 193 zero-shot retrieval tasks across episodic, dialogue, semantic and
  procedural memory. The paper's own framing is that LMEB and MTEB measure orthogonal
  capabilities, so MTEB standing does not carry over to memory retrieval. Adopted as the
  evaluation frame for embedding model selection.
  <!-- lint-allow: doc-numeric-macros -- third-party benchmark's own dataset and task counts, not a build-time stat -->
- [NVIDIA, "Domain-Specific Embedding Fine-Tuning"](https://huggingface.co/blog/nvidia/domain-specific-embedding-finetune):
  synthetic data generation, hard negative mining and contrastive fine-tuning on a single
  GPU with no manual labelling. Reports a 10% gain in Recall@10 and NDCG@10 on a synthetic
  dataset and Recall@60 moving from 0.751 to 0.951 on one real deployment. Informs the
  optional embedding fine-tune pipeline.

### Ecosystem

- [MetaGPT](https://github.com/FoundationAgents/MetaGPT): multi-agent SOP framework.
- [ChatDev](https://github.com/openbmb/ChatDev): multi-agent platform, repositioned away
  from the virtual-software-company framing.
- [CrewAI](https://github.com/crewAIInc/crewAI): role-based crews paired with
  deterministic flows.
- [AutoGen](https://github.com/microsoft/autogen): async multi-agent framework, in
  maintenance.
- [LiteLLM](https://github.com/BerriAI/litellm): unified LLM API gateway.
- [pgvector](https://github.com/pgvector/pgvector): vector similarity search for
  PostgreSQL.
- [sqlite-vec](https://github.com/asg017/sqlite-vec): vector search as a SQLite extension.
- [A2A Protocol](https://github.com/a2aproject/A2A): Agent-to-Agent protocol (Linux
  Foundation).
- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25): Model
  Context Protocol.
- [Langfuse Agent Comparison](https://langfuse.com/blog/2025-03-19-ai-agent-comparison):
  framework comparison.
- [Confluent Event-Driven Patterns](https://www.confluent.io/blog/event-driven-multi-agent-systems/):
  multi-agent architecture patterns.
- [Microsoft Multi-Agent Reference Architecture](https://microsoft.github.io/multi-agent-reference-architecture/):
  enterprise patterns.
- [OpenRouter](https://openrouter.ai/): multi-model API gateway.
