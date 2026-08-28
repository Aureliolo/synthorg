# SynthOrg Documentation

**Describe a piece of software. It gets built in one pass: split into parts, built in
parallel, each part checked by something that did not write it. On your hardware, against
the models you choose.**

!!! warning "Pre-alpha. Read this first."

    SynthOrg is pre-alpha. The loop has been driven live against a real deployment twelve
    times and has never reached the assembly stage: no run has produced an assembled
    deliverable, and no completion has been recorded. Rounds ended on authentication, an
    event-loop split, a provider outage, a review that read its input from a store written
    after it had ruled, a replan cap with no exit, and decomposition bounds that each
    discarded a converged tree.

    Nothing on these pages promises you working software. What they describe is how the
    system is built and how it behaves, which you can check against the code. Expect bugs,
    missing polish, and breaking changes between releases. See the
    [Roadmap](roadmap/index.md) for what is wired versus what is intent.

---

## The problem

An agent working alone cannot hold a whole application. It does one thing at a time, and
the twentieth thing damages the first. Adding agents does not fix that on its own, because
the binding constraint is not agent supply: it is **decomposition quality**. Splitting work
so that the parts are genuinely independent is the hard problem, and it is the one this
system is built around.

## The mechanism

A tree does not have the single-agent failure mode, provided the merges hold. So the
objective is decomposed recursively into a tree of units that can each be built on their
own, the leaves are built concurrently in isolated containers, and the tree is assembled
from the bottom up.

- **Decomposition** turns an objective into a plan: a tree of units, each with declared
  dependencies and expected artefacts. A unit that is not atomic is split again. See
  [Recursive Decomposition](design/recursive-decomposition.md).
- **Dispatch** builds waves from the whole tree's dependency graph, so independent
  subtrees run at the same time and a container lands strictly after the subtree it
  assembles. A unit whose declared inputs died is parked with the reason, never dispatched
  onto dead work. See [Coordination](design/coordination.md).
- **Execution** runs each unit in its own sandboxed container with a scoped workspace and
  a governed tool surface. See [Agent Execution](design/agent-execution.md).
- **Review** is done by something that did not write the work. The reviewer is selected
  from the roster and the executor is excluded, a narrowed identity is dispatched for the
  session, and the archive refuses a verdict row whose reviewer and executor are the same
  agent. An independent check is a triage filter, not an authority: it does not establish
  that the work is correct, and it does not replace your judgement. See
  [Verification Quality](design/verification-quality.md).
- **Assembly** is a gated stage of its own, not an implicit side effect of the last unit
  finishing. Every plan item passing its gate opens an accountable assembly task, and then
  an evaluation pass scored against the plan's own objective criteria. No verdict parks the
  plan; it never completes it. See [Initiative Tail](design/initiative-tail.md).

## Where it runs

Self-hosted, on your hardware. The platform is provider-agnostic: every LLM dispatch names
its own explicit `(provider, model)` pair, including local models, and no default provider
exists to fall back on. Your code does not leave your machine.

---

## Get Started

<div class="grid cards" markdown>

-   :material-play-circle:{ .lg .middle } **Run it**

    ---

    Stand up the platform with Docker and configure it through the dashboard.

    [:octicons-arrow-right-24: User Guide](user_guide.md)

-   :material-rocket-launch:{ .lg .middle } **Quickstart**

    ---

    Install the CLI, start the stack, and complete the setup wizard.

    [:octicons-arrow-right-24: Quickstart](guides/quickstart.md)

-   :material-code-braces:{ .lg .middle } **Develop it**

    ---

    Clone the repository, set up a development environment, and contribute.

    [:octicons-arrow-right-24: Developer Setup](getting_started.md)

-   :material-book-open-page-variant:{ .lg .middle } **Guides**

    ---

    Configuration, providers, budgets, security, deployment, and operations.

    [:octicons-arrow-right-24: Guides](guides/index.md)

</div>

---

## Design Specification

The design pages are the source of truth for designed behaviour. Each area marks its own
wiring status; treat any gap between a page and the code as the work, not the spec.

<div class="grid cards" markdown>

-   **Design Overview**

    ---

    What the system is for, its principles, and the vocabulary the other pages use.

    [:octicons-arrow-right-24: Design Overview](design/index.md)

-   **Recursive Decomposition**

    ---

    How an objective becomes a tree, the atomicity gate, and the recursion bounds.

    [:octicons-arrow-right-24: Decomposition](design/recursive-decomposition.md)

-   **Coordination**

    ---

    Dependency-gated waves, parallel dispatch, parking, and run recovery.

    [:octicons-arrow-right-24: Coordination](design/coordination.md)

-   **Verification Quality**

    ---

    Who reviews, why they cannot be the author, and what a verdict does and does not mean.

    [:octicons-arrow-right-24: Verification](design/verification-quality.md)

-   **Initiative Tail**

    ---

    Assembly and evaluation: the only path by which a plan reaches completion.

    [:octicons-arrow-right-24: Initiative Tail](design/initiative-tail.md)

-   **Agent Execution**

    ---

    The execution loop, sandboxed tool use, context budget, and termination.

    [:octicons-arrow-right-24: Agent Execution](design/agent-execution.md)

-   **Providers & Budget**

    ---

    Explicit provider binding, cost recording, and spending controls.

    [:octicons-arrow-right-24: Providers](design/providers.md)

-   **Security**

    ---

    Autonomy levels, the approval gate, sandboxing, and untrusted-content fencing.

    [:octicons-arrow-right-24: Security](design/security.md)

</div>

---

## What is wired

Every item below is implemented and exercised by deterministic end-to-end harnesses driven
by a scripted provider, so no real spend is involved in the suite. That is a statement
about the code paths, not a claim that a run delivers.

- **Decomposition and dispatch**: recursive planning, a dependency graph over the whole
  tree, parallel waves, and level-triggered recovery after a restart.
- **Independent review**: roster-staffed gate roles, executor exclusion, and archived
  verdicts that record the model each judgement was made under.
- **Sandboxed execution**: per-unit containers, a scoped shared workspace, and a governed
  tool surface including an MCP bridge for external tools.
- **Explicit provider binding**: every dispatch names its own provider and model; an
  unconfigured feature is off and says so rather than borrowing a default.
- **Budgets and cost attribution**: per-agent limits, run and token ceilings, and spend
  attributed by prompt purpose.
- **Approval gate and audit**: actions parked for a human by autonomy level, with a signed
  audit chain.
- **Memory and knowledge**: per-agent and shared memory with a retrieval pipeline, plus an
  ingested external corpus agents can cite.
- **Operator surfaces**: a React dashboard, a REST and WebSocket API, and runtime-editable
  settings.

Roles, departments and staffing are shipped machinery and the pages that document them are
accurate. They are plumbing: how a reviewer is found and how permissions are scoped, not
the reason the system works.

---

## Further Reading

| Section | Description |
|---------|-------------|
| [Architecture](architecture/index.md) | System overview, module map, design principles |
| [Tech Stack](architecture/tech-stack.md) | Technology choices and engineering conventions |
| [Decision Log](architecture/decisions.md) | All design decisions, organised by domain |
| [REST API](openapi/index.md) | REST + WebSocket API reference (Scalar/OpenAPI) |
| [Library Reference](api/index.md) | Auto-generated from docstrings |
| [Roadmap](roadmap/index.md) | Status, open questions, future vision |

---

## Links

- [GitHub Repository](https://github.com/Aureliolo/synthorg)
- [License](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BUSL 1.1, source available; free production use for non-competing small organisations; converts to Apache 2.0 at the Change Date) <!-- lint-allow: doc-numeric-macros -- "Apache 2.0" is a license version, not a stat claim -->
- [Licensing & Usage](licensing.md): what is permitted, why BUSL, and how to get a commercial license
