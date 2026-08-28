<p align="center">
  <strong>SynthOrg</strong><br>
  <em>A single agent cannot hold a whole application. SynthOrg splits it.</em>
</p>

<p align="center">
  <a href="https://securityscorecards.dev/viewer/?uri=github.com/Aureliolo/synthorg"><img src="https://api.securityscorecards.dev/projects/github.com/Aureliolo/synthorg/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://slsa.dev"><img src="https://slsa.dev/images/gh-badge-level3.svg" alt="SLSA 3"></a>
  <a href="https://codecov.io/gh/Aureliolo/synthorg"><img src="https://codecov.io/gh/Aureliolo/synthorg/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://codspeed.io/Aureliolo/synthorg?utm_source=badge"><img src="https://img.shields.io/endpoint?url=https://codspeed.io/badge.json" alt="CodSpeed Badge"></a>
  <a href="https://github.com/Aureliolo/synthorg/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL_1.1_(source_available)-blue" alt="License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python"></a>
  <a href="https://synthorg.io/docs"><img src="https://img.shields.io/badge/docs-synthorg.io-purple" alt="Docs"></a>
</p>

---

A single agent in a loop cannot hold a whole application. It does one thing at a time, and the twentieth thing damages the first, which is why an assistant that codes well in a small repository degrades as the application around it grows. The binding constraint on building software with agents is decomposition quality, not agent supply.

SynthOrg is built around that constraint. Describe a piece of software, and the work is decomposed recursively into a tree of independently buildable units; the leaves are built concurrently in isolated containers and assembled from the bottom up, and each part is checked by something that did not write it. Splitting the work so the parts are genuinely independent is the hard problem, and it is the one the system is organised around.

It is self-hosted: your hardware, your provider credentials, and no SynthOrg service in the path. Point it at a hosted model provider and that provider receives what it needs, under your own key, exactly as from any other client; point it at local models and nothing leaves the machine.

It is provider-agnostic (<!--RS:providers_via_litellm-->95+<!--/RS--> LLM providers via [LiteLLM](https://github.com/BerriAI/litellm)), configuration-driven ([Pydantic v2](https://docs.pydantic.dev/) models), and licensed BUSL-1.1 (converts to Apache 2.0 at the Change Date).

> **Project status (read this).** SynthOrg is **pre-alpha**, and not yet fully working. The honest headline is the loop, not the platform: driven live against a real deployment, it has never reached the assembly stage, and no run has produced an assembled deliverable. The failure modes so far are open in the issue tracker.
>
> The platform underneath is built and tested (<!--RS:tests-->46,000+<!--/RS--> tests, 80%+ coverage): API, dashboard, CLI, dual-backend persistence, the provider layer, the agent runtime, the multi-agent coordinator, the work pipeline spine, the intake engine, sandbox lifecycle dispatch, and the distributed-path consumers are all wired and exercised by deterministic e2e harnesses with a scripted provider (no real LLM spend). Use it for research and contribution, not for production workloads. Progress is tracked openly on the [roadmap](https://synthorg.io/docs/roadmap/) and the [issue tracker](https://github.com/Aureliolo/synthorg/issues).

## How it works

- **The work is split before any of it is built.** An objective is decomposed recursively into a tree of units that can be built independently. The leaves are built concurrently, each agent in its own container and its own git worktree, and the tree is assembled from the bottom up. The parallelism is not there for speed: a tree does not have the failure mode a single loop has, provided the merges hold. No size is claimed here, because the decomposition ceiling is still being measured.
- **Each part is checked by something that did not write it.** The author cannot be the reviewer, structurally rather than by convention: selection excludes the agent that executed the work, and a database constraint rejects a verdict whose reviewer is its executor. Nothing binds the two to different model families, so a pair can still share a blind spot. An independent judge is a triage filter, not an authority.
- **It runs where you put it.** Self-hosted, provider-agnostic, local models included. Nothing about the design assumes a hosted control plane, and there is no SynthOrg service in the path.

## How supervision works

Autonomy here is not trust. An agent never earns latitude by behaving well; latitude is a rule you write, and the rules accumulate as you learn which actions you can stop looking at.

- **Oversight mode, set by you.** Four modes, most oversight to least: `locked` (a human approves every action), `supervised` (reads and test runs flow, every mutation is approved), `semi` (code, tests, docs, commits and internal messages flow; deploys, publishes, budget, org changes and tool use are approved), and `full` (approval routing off; the deny list and the built-in detectors still run). A mode applies per agent, per initiative, per department, or company-wide, most specific winning. Moving an initiative to `full` is a CEO-only opt-in that must be confirmed explicitly and is audited at warning level, and a lookup failure on that path falls back to `locked` rather than opening up.
- **A gate on every action.** A deterministic deny list refuses the unconditional catastrophes (production deploys, database administration, firing an agent), a deterministic allow list clears the obviously safe, built-in detectors catch credential leaks, path traversal and destructive operations with no configuration at all, policy rules you write extend that, and an LLM evaluator handles what is left, attaching a reason to its verdict. Clearing the gate is not the same as running: the oversight mode still routes what cleared it, so under `locked` every action waits for you regardless. Rule evaluation fails closed, and an internal error there denies at critical risk rather than letting the action through.
- **Latitude is granted, never earned.** No agent, the CEO included, can widen its own limits: a request to raise an agent's autonomy becomes an approval item for a human. What actually buys you back attention is the allow list growing: the commands, rules, tools and conditions you ratify as safe, each one a decision you made once and never have to make again.
- **Done means proven.** Work does not complete because an agent says so. The completion oracle is on by default: the build and tests recorded against the task must pass, and an independent reviewer (never the author, excluded at selection and refused by a database constraint) must sign off. Both halves fail closed, short of a persistence-less boot with no record store to read, and an initiative reaches completion only through assembly and evaluation, never straight from execution.
- **Spend has a ceiling.** A run that reaches its hard cost ceiling parks itself and waits for you to raise the ceiling or stop it, rather than spending through the limit.

Approvals queue in the dashboard and over the REST API, and a reply through the chat integration decides a run parked at the approval gate. A run parked on spend is different: it resumes only when an operator names a new ceiling through the budget API. The full model lives in the [security](https://synthorg.io/docs/design/security/) and [verification and quality](https://synthorg.io/docs/design/verification-quality/) design pages.

## What is available now

A tested platform you can run, inspect, and build on:

- **REST + WebSocket API** (Litestar) and a **React 19 dashboard** (org chart, task board, agent detail, budget tracking, provider management, workflow editor, setup wizard) with live WebSocket / SSE updates.
- **Go CLI** for Docker orchestration: `init`, `start`, `stop`, `status`, `logs`, `update`, `doctor`, `uninstall`, `version`, `config`, `wipe`, `cleanup`, `worker`, `backup`, `new`, `completion-install`, with cosign signature and SLSA provenance verification at pull time.
- **Dual-backend persistence**: SQLite (single-node default) and PostgreSQL (multi-instance), conformance-tested for parity, with in-process yoyo schema migrations and ISO 4217 currency stamping on every cost-bearing row.
- **Provider layer**: any LLM via LiteLLM with built-in retry and rate-limit handling; local model management for Ollama and LM Studio; periodic model-refresh that flags removed models stale, surfaces in-family upgrade recommendations for review, and optionally auto-applies within-family upgrades.
- **Configuration and templates**: define a company in YAML; importable/shareable agent, department, and company templates.
- **Agent runtime**: a configured provider boots a real agent runtime that executes tasks (LLM + sandboxed tools) under a minimal safety spine (a gate verdict on every tool action, approval-queue producer for sensitive actions). An empty company (no provider) cleanly rejects task submission. Exercised by a deterministic e2e simulation harness (synthetic clients, scripted provider, zero LLM spend).
- **Multi-agent coordinator + work pipeline spine**: `/coordinate` runs decompose, route, parallel execution, then rollup end to end behind the provider-present switch. The shared work pipeline (intake to projects to decompose to solo/team to execute to coordination metrics) is the single integration point every entry adapter feeds, with solo-vs-team decided internally by decomposition.
- **Entry adapters**: work-entry paths into the pipeline spine. Stated objectives (`POST /objectives`) are the always-on operator door, each standing up its own per-initiative project; the task board (`POST /tasks`) files against a caller-named project; the synthetic-client intake door (`POST /requests/{id}/approve`) is a benchmark surface, off by default behind `simulations.client_intake_enabled`.
- **Sandbox lifecycle dispatch**: `DockerSandbox.execute()` honours `owner_id` and dispatches to the configured per-call / per-agent / per-task lifecycle strategy, with grace-period teardown.
- **Operations**: structured logging with redaction and correlation, Prometheus metrics and OTLP, HttpOnly-cookie multi-user sessions with CSRF protection, Wolfi apko-composed distroless images with Trivy scanning, cosign signatures, and SLSA L3 provenance.
- **Distributed dispatch**: NATS JetStream queue, worker pool, dead-letter consumer, dedup pruner, and heartbeat subscriber, validated under multi-worker synthetic load (no loss, no duplication).
- **Conversational interface**: talk to the running system in natural language. Explain-chat answers grounded in the live state (in-flight tasks, active projects, pending approvals), citing the records it draws on rather than inferring idleness. Standing up an initiative is never a classifier's guess: it goes through a charter interview whose draft you approve, and that approval is what authorises the work and the spend behind it. Clarify-and-propose steers work a charter already authorised, and steering directives park individually in the approval queue. Also per-turn concern-routing to the role agent that fits, multi-agent group chat, human-consented agent-initiated invites, direct MCP acting under trust (sensitive actions approval-gated; fail-closed when security governance is inactive), and an operator console that configures the control plane conversationally (connect an integration, flip toggles) with credentials captured out of band so they never reach the transcript. Explain-chat, propose, and group chat are on by default and toggle live per request. Concern routing also resolves live per turn (no restart), but additionally requires the off-by-default persona master switch, so it stays off until that is enabled even though its own toggle defaults on. Agent-initiated invites, direct MCP acting, and the operator console are off by default. Exercised by deterministic e2e harnesses with a scripted provider.
- **Delivery substrate**: persistent project workspace with pluggable git, brownfield codebase intake, living documentation, and a deep requirements interview.
- **Operate tier**: a golden-company benchmark, mission control with run replay, a cost forecast and hard-ceiling dial, a measurable learning curve, deterministic replay, run narratives, and an adversarial red-team.
- **Agent capability layer**: knowledge and provenance retrieval substrate, research mode, continual improvement, governed external API access, headless-browser and virtual-desktop testing.

## In active development

The runtime, coordinator, intake, work pipeline, sandbox dispatch, and distributed-path consumers are wired and exercised by deterministic harnesses. What is in flight is everything between a wired runtime and a build that lands:

- **Getting the loop through assembly**: live rounds fail before the tree is assembled, so this is the work. Each failure is diagnosed to a root cause and fixed rather than retried, and the failure modes are open in the issue tracker.
- **Iterating on an existing build**: adding a requirement to something already built, without breaking what works, is design intent. It is not built, and nothing in the product does it today.
- **Several builds side by side**: nothing caps how many initiatives are active together, and parallelism inside one initiative is exercised (waves of agents, each in its own isolated git worktree). Running several builds at once is not an operator surface: it is not presented anywhere in the product and has not been exercised.
- **Self-improvement loop**: system-wide signals from existing subsystems producing deployment and product-level improvement proposals through a rule-first hybrid pipeline with mandatory human approval. Components built and unit-tested; live end-to-end run pending.
- **Real-provider acceptance**: the e2e harness drives the runtime against a deterministic scripted provider, not a real LLM. Live rounds against real providers are driven by hand; a repeatable golden-company benchmark and run narrative arrive with the operate tier.

The design for each lives in the [Design Specification](https://synthorg.io/docs/design/).

## Quick Start

### Install

```bash
# Linux / macOS
curl -sSfL https://synthorg.io/get/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://synthorg.io/get/install.ps1 | iex
```

### Run

```bash
synthorg init                                   # interactive setup wizard (SQLite default)
synthorg init --persistence-backend postgres    # auto-provision a Postgres container
synthorg start                                  # pull images + start containers
```

Open [localhost:3000](http://localhost:3000); the **setup wizard** covers LLM providers, company config (currency, budget, model-spend profile), agent setup with per-agent model matching, coordinator + embedding model selection, and theme selection. Choose **Guided Setup** for the full experience or **Quick Setup** (provider + company name only). This brings up the platform and dashboard. A configured provider is what boots the agent runtime, so skipping provider setup yields an empty company by design: it stores the organisation and cleanly rejects task submission.

**Persistence backends:** SQLite (default) for single-node and development, Postgres for multi-instance deployments. The CLI orchestrates both. `--persistence-backend postgres` generates a `dhi.io/pgvector` DHI service (a hardened Postgres image bundling pgvector, so semantic memory has a dense index; image tag and digest pinned via `DefaultPostgresImageTag` and `DefaultPostgresImageDigest` in `cli/internal/config/state.go`), random credentials, and a named data volume. `synthorg stop` preserves the data volume unless `--volumes` is passed.

### From source

```bash
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
uv sync                  # install dev + test deps
uv sync --group docs     # install docs toolchain
```

Schema migrations run in-process via [yoyo-migrations](https://ollycope.com/software/yoyo/latest/) (installed by `uv sync`); no external binary required. Building the docs site locally (for D2 diagrams) additionally requires the [D2 CLI](https://d2lang.com/tour/install) on `PATH`.

### Docker Compose (manual)

```bash
cp docker/.env.example docker/.env
docker compose -f docker/compose.yml up -d
curl http://localhost:3001/api/v1/readyz
```

## Target architecture

The diagram below is the designed architecture. The
[Design Specification](https://synthorg.io/docs/design/) states the current
wiring status per area.

```mermaid
graph TB
    Config[Config & Templates] --> Engine[Agent Engine]
    Engine --> Core[Core Models]
    Engine --> Providers[LLM Providers]
    Engine --> Communication[Communication]
    Engine --> Tools[Tools & MCP]
    Engine --> Memory[Memory]
    Engine --> Security[Security & Trust]
    Engine --> Budget[Budget & Cost]
    Engine --> HR[HR Engine]
    Meta[Meta-Loop] --> Engine
    Meta --> HR
    Meta --> Budget
    API[REST & WebSocket API] --> Engine
    API --> Meta
    Dashboard[React Dashboard] --> API
    CLI[Go CLI] --> API
    Observability[Observability] -.-> Engine
    Persistence[Persistence] -.-> HR
    Persistence -.-> Security
    Persistence -.-> Engine
```

## Compare

SynthOrg vs [other agent frameworks](https://synthorg.io/compare/) across multi-agent coordination, memory, budget tracking, security, and observability. The comparison marks SynthOrg capabilities honestly as available now versus planned, matching the preceding status sections. <!-- lint-allow: doc-numeric-macros -- competitor count is sourced from data/competitors.yaml via generate_comparison.py, not runtime_stats -->

## Documentation

| Section | What's there |
|---------|-------------|
| [User Guide](https://synthorg.io/docs/user_guide/) | Install, configure, run, customise |
| [Guides](https://synthorg.io/docs/guides/) | Quickstart, company config, agents, budget, security, MCP tools, deployment, logging, memory |
| [Design Specification](https://synthorg.io/docs/design/) | The designed behaviour of every subsystem (the source of truth; states current wiring status per area) |
| [Architecture](https://synthorg.io/docs/architecture/) | System overview, tech stack, decision log |
| [REST API](https://synthorg.io/docs/openapi/) | Scalar/OpenAPI reference |
| [Library Reference](https://synthorg.io/docs/api/) | Auto-generated from docstrings |
| [Security](https://synthorg.io/docs/security/) | App security, container hardening, CI/CD security |
| [Licensing](https://synthorg.io/docs/licensing/) | BUSL 1.1 terms, Additional Use Grant, commercial options |
| [Roadmap](https://synthorg.io/docs/roadmap/) | Current status, what works today, what is in active development |

> **Contributors:** Start with the [Design Specification](https://synthorg.io/docs/design/) before implementing any feature. See [`DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) for the full design set. The design pages describe intended behaviour and mark per-area current wiring status; treat any gap between a spec and `src/` as the work, not the spec.
>
> **Forking?** CI runs out of the box for code changes; the release pipeline needs setup (environments, labels, branch protection, a release-bot GitHub App). On your first push, the **Ops - Preflight** workflow opens a tracking issue listing exactly what is missing; see [Fork Setup](https://synthorg.io/docs/guides/fork-setup/) for the long-form walkthrough.

## License

[Business Source License 1.1](LICENSE): free production use for non-competing organisations with fewer than 500 employees and contractors. Converts to Apache 2.0 on the change date specified in [LICENSE](LICENSE). See [licensing details](https://synthorg.io/docs/licensing/) for the full rationale and what is permitted.
