# SynthOrg - High-Level Design Specification

> A framework for building synthetic organisations: autonomous AI agents orchestrated as a virtual company, with configurable roles, hierarchies, communication patterns, and tool access.

---

The design specification has been split into focused documentation pages for better navigation and maintainability. Each page covers a cohesive domain of the framework's design.

## Design Pages

| Page | Sections | Description |
|------|----------|-------------|
| [Design Overview](design/index.md) | Vision, Core Concepts | What SynthOrg is, design principles, glossary |
| [Agents](design/agents.md) | Agent Identity | Identity card, personality (OCEAN + behavioural enums), skill model, tool namespaces, identity versioning |
| [HR & Agent Lifecycle](design/hr-lifecycle.md) | HR | Seniority, role catalog, hiring (templates + LLM), pruning, dynamic scaling, firing, performance, evaluation, promotions, evolution, five-pillar framework, client agents |
| [Organisation & Templates](design/organization.md) | Company Structure, Templates | Company types, hierarchy, departments, template system |
| [Communication](design/communication.md) | Communication Architecture | Message bus transport, patterns, message format, config and lifecycle |
| [Communication A2A Gateway](design/communication-a2a.md) | External Federation | Optional gateway, agent cards, concept mapping, SSE streaming, outbound client |
| [Communication Coordination](design/communication-coordination.md) | Orchestration | Loop prevention, conflict resolution, meeting protocols, scheduler, MCP facades, failure guardrails |
| [Communication Event Stream](design/communication-events.md) | Observability + HITL | AG-UI projection, SSE endpoint, interrupt/resume, EvidencePackage, async delegation, citations |
| [Distributed Runtime](design/distributed-runtime.md) | Transport Evaluation, Bus Backend, Task Queue, Migration | Pluggable distributed backend design, NATS JetStream first implementation, distributed task queue hook into TaskEngine |
| [Task & Workflow Engine](design/engine.md) | Task Engine Core | Task lifecycle, routing, workflow types and definitions, TaskEngine centralised state coordination |
| [Agent Execution](design/agent-execution.md) | Execution Loops | Execution status, ReAct/Plan-Execute/Hybrid loops, prompt profiles, stagnation detection, context budget, brain/hands/session |
| [Coordination & Resilience](design/coordination.md) | Multi-agent + Recovery | Crash recovery, graceful shutdown, workspace isolation, reproducible per-project environments, task decomposability, coordination topology |
| [Verification & Quality](design/verification-quality.md) | Quality Pipeline | Verification stage, harness middleware, review pipeline, intake engine |
| [Memory](design/memory.md) | Memory | Memory types, backends, retrieval, embedding selection, consolidation |
| [Memory Organisational](design/memory-organizational.md) | Shared Knowledge | Company-wide policies, ADRs, OrgMemoryBackend protocol, research directions |
| [Memory Operational](design/memory-operational.md) | Operational Data Persistence | PersistenceBackend protocol, per-entity repositories, SQLite + Postgres, multi-tenancy, invariants |
| [Memory Learning](design/memory-learning.md) | Learning + Injection | Procedural memory auto-gen, capture / pruning / propagation strategies, injection strategies, MemoryService |
| [Living Documentation](design/living-documentation.md) | Per-project wiki + RAG | Dual-purpose living docs (status reports, deliverables, knowledge notes): git-versioned workspace store, PROJECT_DOC RAG namespace, agent write tools + MCP, dashboard wiki |
| [Long-Horizon Project Brain](design/project-brain.md) | Per-project decision memory | Append-only brain of decisions, open questions, blockers, risks, dependencies, and plan revisions: SQL revision chain + git-versioned snapshots, PROJECT_BRAIN RAG namespace fenced under brain-state on retrieval, agent write/search tools + operator MCP + read-only REST, boot replay of unindexed or stale entries |
| [Knowledge and Provenance Substrate](design/knowledge-substrate.md) | Multi-source RAG with citations | Multi-source ingestion (PDF, web, repos, tickets, design docs), AST-aware code chunking + section-aware document chunking, hybrid retrieval (dense + BM25 + RRF), provenance locators (PDF page/region, code line span, web URL offset, ticket comment), freshness re-index on source change, SEC-1 wrapping of untrusted ingested content |
| [Brownfield Codebase Intake](design/brownfield-intake.md) | Merger/acquisition entry mode | Import an existing repo (git seed) into a persistent workspace, deterministic per-ecosystem structure map (modules/entry points/tests/build/deps), agent analysis pass producing a CODEBASE_ANALYSIS deliverable, knowledge indexing, SSRF + forge-token source resolution, re-import policy |
| [Persistence](design/persistence.md) | Persistence | Repository protocol, SQLite/Postgres backends, time-series tables, TimescaleDB, migrations |
| [Multi-Agent Memory Consistency](design/memory-consistency.md) | Consistency Model | Append-only writes, MVCC snapshot reads, conflict handling, deployment rollout |
| [Semantic Ontology](design/ontology.md) | Entity Definitions, Versioning, Drift | Shared vocabulary, decorator, backend, bootstrap, drift detection |
| [Providers](design/providers.md) | Provider abstraction, routing | LLM provider layer, LiteLLM integration, multi-provider resolution |
| [Budget & Cost](design/budget.md) | Budget hierarchy, cost tracking, CFO, reporting | Per-agent cost enforcement, quota degradation, risk budget, PTE |
| [Tools & Capabilities](design/tools.md) | Tool categories, sandboxing, MCP, trust | Layered sandbox, progressive disclosure, action types, access levels |
| [Integrations](design/integrations.md) | OAuth flows, MCP catalog, webhooks, tunnel, health | External service integrations: OAuth provider connections, MCP server catalog + install, outbound webhooks, ngrok-style tunnel, integration-health rollups |
| [A2A Protocol](design/a2a-protocol.md) | Agent-to-agent transport | Agent Card discovery, capability negotiation, signed envelope, well-known JWKS, gateway authentication |
| [Security & Approval](design/security.md) | Approval workflow, autonomy, output scanning, policy engine | Fail-closed rule engine, review gates, credential isolation, A2A auth |
| [Observability](design/observability.md) | Performance tracking, structured logging, correlation, event taxonomy | 11 default sinks, Prometheus / OTLP export, runtime-editable levels |
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
