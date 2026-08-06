# SynthOrg - High-Level Design Specification

> A framework for building synthetic organisations: role-based AI agents run as a supervised company, with configurable roles, hierarchies, communication patterns, and tool access, under an oversight mode the operator sets.

---

The design specification has been split into focused documentation pages for better navigation and maintainability. Each page covers a cohesive domain of the framework's design.

## Design Pages

| Page | Sections | Description |
|------|----------|-------------|
| [Design Overview](design/index.md) | Vision, Core Concepts | What SynthOrg is, design principles, glossary |
| [Agents](design/agents.md) | Agent Identity | Identity card, personality (OCEAN + behavioural enums), skill model, tool namespaces, identity versioning |
| [HR & Agent Lifecycle](design/hr-lifecycle.md) | HR | Role catalog, reporting-graph authority, hiring (templates + LLM), pruning, dynamic scaling, firing, performance, evaluation, evolution, five-pillar framework, client agents |
| [Evaluation Loop](design/evaluation-loop.md) | Closed-loop agent evaluation | Trace capture, behaviour tagging, five-pillar enrichment, targeted-fix proposals layered on performance tracking and trajectory scoring; pluggable external benchmarks including the model-pin-validation golden-snapshot drift gate |
| [Organisation & Templates](design/organization.md) | Company Structure, Templates | Company types, hierarchy, departments, template system |
| [Communication](design/communication.md) | Communication Architecture | Message bus transport, patterns, message format, config and lifecycle |
| [Communication A2A Gateway](design/communication-a2a.md) | External Federation | Optional gateway, agent cards, concept mapping, SSE streaming, outbound client |
| [Communication Coordination](design/communication-coordination.md) | Orchestration | Loop prevention, conflict resolution, meeting protocols, scheduler, MCP facades, failure guardrails |
| [Communication Event Stream](design/communication-events.md) | Observability + HITL | AG-UI projection, SSE endpoint, interrupt/resume, EvidencePackage, async delegation, citations |
| [Distributed Runtime](design/distributed-runtime.md) | Transport Evaluation, Bus Backend, Task Queue, Migration | Pluggable distributed backend design, NATS JetStream first implementation, distributed task queue hook into TaskEngine |
| [Task & Workflow Engine](design/engine.md) | Task Engine Core | Task lifecycle, routing, workflow types and definitions, TaskEngine centralised state coordination |
| [Agent Execution](design/agent-execution.md) | Execution Loops | Execution status, ReAct/OpenHands loops, prompt profiles, stagnation detection, context budget, brain/hands/session |
| [OpenHands Execution Loop](design/openhands-loop.md) | Selectable second inner loop | Phase 0 harness survey, OpenHands adapter over `ExecutionLoop`, event->TurnRecord mapping, in-sandbox stdin/stdout run protocol, budget/shutdown/cancellation at event boundaries, NO_OP rule, task-level resume, image-only dependency isolation |
| [Inner-Loop A/B Harness](design/loop-ab-harness.md) | Loop promotion evidence | Scored A/B matrix over the shipped inner loops, workspace-graded executable briefs, correctness gate, tokens-rank/dollars-breakdown rubric, commit-stamped scoreboard, promotion recommendation feeding `engine.default_loop_type` / `engine.loop_complexity_overrides` |
| [LLM Gateway](design/llm-gateway.md) | OpenAI-compatible governance boundary | Router mounted on the API app fronting the ProviderRegistry: per-run signed bearer, Explicit Provider Binding, cost + prompt-purpose attribution, hard token-budget kill, SEC-1 log redaction |
| [Credentialed-tool MCP](design/credentialed-mcp.md) | Governed streamable-HTTP MCP boundary | Scoped forge/chat/deploy/publish tools to an embedded harness with host-side credential brokering, ConnectionApprovalGate + ActionSignature, egress pin, SEC-1 output fencing, approval-parking UX; per-environment deploy and per-channel publish action types + destructive-tool guardrail (confirm+reason+actor) |
| [Coordination & Resilience](design/coordination.md) | Multi-agent + Recovery | Crash recovery, graceful shutdown, workspace isolation, reproducible per-project environments, task decomposability, coordination topology |
| [Mid-Flight Steering](design/mid-flight-steering.md) | In-run operator intervention | Steering directives (hint / redirect), steering store, adoption at safe execution boundaries, project-brain integration, agent-execution links |
| [The Org Asks](design/org-questions.md) | In-run agent-to-operator question | Standing "ask rather than guess" directive at every autonomy level, declared reversibility on both human-input tools, the parked question surfaced and answered in the unified conversation |
| [Verification & Quality](design/verification-quality.md) | Quality Pipeline | Verification stage, harness middleware, review pipeline, intake engine |
| [Plan Review](design/plan-review.md) | Durable Plan Entity | First-class `Plan`/`PlanItem` model, `PlanStatus` lifecycle, `/plans` API, decomposition projection, review workspace, dispatch-on-approval |
| [Project Lifecycle](design/project-lifecycle.md) | Initiative Graph | Project/plan/task linkage, `ProjectStatus` lifecycle (no failed state, tail mirrored from the plan), completion rule, verification-derived rollup, progress + critical-path surface |
| [Initiative Tail](design/initiative-tail.md) | Integrate + Evaluate | The stages between "every item done" and delivery: the gated assembly task, the evidenced per-criterion evaluation (fail-closed), the stalled-initiative auto-replan and its generation cap, degraded-boot parking |
| [Memory](design/memory.md) | Memory | Memory types, backends, retrieval, embedding selection, consolidation |
| [Memory Organisational](design/memory-organizational.md) | Shared Knowledge | Company-wide policies, ADRs, OrgMemoryBackend protocol, research directions |
| [Memory Operational](design/memory-operational.md) | Operational Data Persistence | PersistenceBackend protocol, per-entity repositories, SQLite + Postgres, multi-tenancy, invariants |
| [Memory Learning](design/memory-learning.md) | Learning + Injection | Procedural memory auto-gen, capture / pruning / propagation strategies, injection strategies, MemoryService |
| [Living Documentation](design/living-documentation.md) | Per-project wiki + RAG | Living docs (status reports, deliverables, knowledge notes, codebase analyses, run narratives): git-versioned workspace store, PROJECT_DOC RAG namespace, agent write tools + MCP, dashboard wiki |
| [Long-Horizon Project Brain](design/project-brain.md) | Per-project decision memory | Append-only brain of decisions, open questions, blockers, risks, dependencies, and plan revisions: SQL revision chain + git-versioned snapshots, PROJECT_BRAIN RAG namespace fenced under brain-state on retrieval, agent write/search tools + operator MCP + read-only REST, boot replay of unindexed or stale entries |
| [Knowledge and Provenance Substrate](design/knowledge-substrate.md) | Multi-source RAG with citations | Multi-source ingestion (PDF, web, repos, tickets, design docs), AST-aware code chunking + section-aware document chunking, hybrid retrieval (dense + BM25 + RRF), provenance locators (PDF page/region, code line span, web URL offset, ticket comment), freshness re-index on source change, SEC-1 wrapping of untrusted ingested content |
| [Brownfield Codebase Intake](design/brownfield-intake.md) | Merger/acquisition entry mode | Import an existing repo (git seed) into a persistent workspace, deterministic per-ecosystem structure map (modules/entry points/tests/build/deps), agent analysis pass producing a CODEBASE_ANALYSIS deliverable, knowledge indexing, SSRF + forge-token source resolution, re-import policy |
| [Research Mode](design/research-mode.md) | Multi-source research pipeline | `ResearchService.run()` pipeline (query planning, multi-source retrieval, credibility triage, deduplication, synthesis), recording and deterministic replay |
| [Persistence](design/persistence.md) | Persistence | Repository protocol, SQLite/Postgres backends, time-series tables, TimescaleDB, migrations |
| [Multi-Agent Memory Consistency](design/memory-consistency.md) | Consistency Model | Append-only writes, MVCC snapshot reads, conflict handling, deployment rollout |
| [Semantic Ontology](design/ontology.md) | Entity Definitions, Versioning, Drift | Shared vocabulary, decorator, backend, bootstrap, drift detection |
| [Providers](design/providers.md) | Provider abstraction, routing | LLM provider layer, LiteLLM integration, multi-provider resolution |
| [Budget & Cost](design/budget.md) | Budget hierarchy, cost tracking, CFO, reporting | Per-agent cost enforcement, quota degradation, risk budget, PTE |
| [LLM Call Analytics & Coordination Metrics](design/coordination-metrics.md) | Per-call tracking, orchestration ratio, per-purpose cost/latency alerts, coordination error taxonomy | Call categorisation, the coordination metrics suite, and the multi-agent tuning signals complementing budget controls |
| [Tools & Capabilities](design/tools.md) | Tool categories, sandboxing, MCP, trust | Layered sandbox, progressive disclosure, action types, access levels |
| [Toolsmith (Self-Extending Toolkit)](design/toolsmith.md) | Runtime MCP tool-surface extension | Capability-gap detection, governed proposal/apply cycle, human-approval gating for new tools |
| [Integrations](design/integrations.md) | OAuth flows, MCP catalog, webhooks, tunnel, health | External service integrations: OAuth provider connections, MCP server catalog + install, outbound webhooks, ngrok-style tunnel, integration-health rollups |
| [A2A Protocol](design/a2a-protocol.md) | Agent-to-agent transport | Agent Card discovery, capability negotiation, signed envelope, well-known JWKS, gateway authentication |
| [Security & Approval](design/security.md) | Approval workflow, autonomy, output scanning, policy engine | Fail-closed rule engine, review gates, credential isolation, A2A auth |
| [Observability](design/observability.md) | Performance tracking, structured logging, correlation, event taxonomy | 11 default sinks, Prometheus / OTLP export, runtime-editable levels |
| [Subsystem Reconciliation](design/subsystem-reconciliation.md) | Declarative wiring, level-triggered convergence | `SubsystemSpec` requires / provides / settings / rebuild_on_change, boot as the first pass, periodic resync, `GET /subsystems` phases |
| [Notifications](design/notifications.md) | NotificationSink protocol, dispatcher, adapters | Console / ntfy / Slack / email adapters, severity filtering |
| [Backup & Restore](design/backup.md) | Component handlers, manifests, scheduler, retention | SQLite VACUUM INTO snapshots, validated restore with safety backup |
| [Deployment](design/deployment.md) | Container runtime, image verification, sandbox resolution | apko-composed Wolfi bases, cosign + SLSA L3, Caddy web server |
| [Web HTTP Adapter](design/web-http-adapter.md) | HTTP Transport | Axios XHR vs fetch, MSW interceptor, test teardown contract |
| [Web Active-Handle Detection](design/web-active-handle-detection.md) | Per-Test Resource-Leak Gate | `async_hooks` snapshot + diff per test, fail-mode default, telemetry artifact, ESLint companion rules |
| [Brand Identity & UX](design/brand-and-ux.md) | Brand, Themes, Colours, Typography, Density, Animation | Visual identity, semantic colour system, theme architecture |
| [Page Structure & IA](design/page-structure.md) | Pages, Navigation, Routing, WebSocket, Responsive | Page list, sidebar hierarchy, URL routing map, WS subscriptions |
| [UX Design Guidelines](design/ux-guidelines.md) | Colour System, Components, Interaction, Animation, Accessibility, Responsive | Implementable specs for the web dashboard |
| [UX Research](design/ux-research.md) | Framework Decision, Migration | Vue-to-React evaluation, decision rationale, migration timeline |
| [Ceremony Scheduling](design/ceremony-scheduling.md) | Strategies, Protocols, Velocity | Pluggable ceremony scheduling, 8 strategies, velocity calculation |
| [Client Simulation](design/client-simulation.md) | Client Types, Intake, Review Pipeline, Simulation | Synthetic client framework for workload generation and evaluation |
| [Strategy & Trendslop Mitigation](design/strategy.md) | Lenses, Principles, Confidence, Impact | Anti-trendslop mitigation for strategic agents |
| [Output-Style Policy](design/output-style-policy.md) | House writing style, hard guardrail, enforcement modes, sanctioned exemptions | Configurable house style injected into agent prompts, plus a deterministic hard guardrail that rejects or rewrites agent output violating a hard rule (the no-em-dash ban) at every output boundary |
| [Self-Improvement](design/self-improvement.md) | Meta-Loop, Signals, Rules, Proposals, Rollout | Self-improving company: signal aggregation, rule engine, improvement proposals, staged rollout |
| [Internationalization](design/internationalization.md) | Locale Resolution, UI Text, Translation Scope | British English UI default, locale-aware Intl-based display formatting, no planned translation framework |

## Supporting Pages

| Page | Description |
|------|-------------|
| [Tech Stack](architecture/tech-stack.md) | Technology choices and engineering conventions |
| [Decision Log](architecture/decisions.md) | All design decisions, organised by domain |
| [Research & Prior Art](reference/research.md) | Framework comparison and scaling research |
| [Industry Standards](reference/standards.md) | MCP, A2A, and other standards |
| [ACG Glossary](architecture/acg-glossary.md) | Bidirectional ACG-to-SynthOrg concept mapping |
| [Open Questions & Risks](roadmap/open-questions.md) | Unresolved questions and risk mitigations |
| [Future Vision](roadmap/future-vision.md) | Backlog features and scaling path |
