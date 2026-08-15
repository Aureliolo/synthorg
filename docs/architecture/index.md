# Architecture Overview

SynthOrg is organised as a modular, protocol-driven framework. Every major subsystem is defined by a protocol interface, enabling pluggable strategy implementations.

## Module Map

```d2
Core: Core Models
Providers: LLM Providers
API: API Layer
ProjectBrain: Project Brain
DocsEngine: Living Docs
A2A: A2A Protocol

Config -> Engine
Engine -> Core
Engine -> Providers
Engine -> Communication
Engine -> Tools
Engine -> Memory
Engine -> Security
Engine -> Budget
Engine -> HR
Engine -> Knowledge
Engine -> Ontology
Engine -> ProjectBrain
Engine -> DocsEngine
Communication -> A2A
API -> Engine
Meta -> Engine {style.stroke-dash: 5}
Ontology -> Memory {style.stroke-dash: 5}
Knowledge -> Memory {style.stroke-dash: 5}
Observability -> Engine {style.stroke-dash: 5}
Observability -> Providers {style.stroke-dash: 5}
Observability -> Security {style.stroke-dash: 5}
Persistence -> HR {style.stroke-dash: 5}
Persistence -> Security {style.stroke-dash: 5}
Templates -> Config
```

## Module Responsibilities

| Module | Purpose |
|--------|---------|
| **core** | Shared domain models: Agent, Task, Role, Company, Project, Approval, Artifact |
| **client** | Client simulation: profiles, requirements, feedback, AI/human/hybrid clients, requirement generators, feedback strategies, pool strategies |
| **engine** | Agent orchestration: execution loops (ReAct, OpenHands), task decomposition, routing, assignment, parallel execution, recovery, shutdown |
| **engine.intake** | Intake processing: `IntakeStrategy` protocol, validation, and lifecycle orchestration for `ClientRequest` objects (the `ClientRequest` type is defined in `client`) |
| **engine.review** | Review pipeline: ReviewStage protocol, multi-stage review orchestration, verdict tracking |
| **providers** | LLM provider abstraction: LiteLLM adapter, capability matching, routing strategies (5), retry + rate limiting |
| **communication** | Inter-agent messaging: bus, dispatcher, delegation, loop prevention, conflict resolution (4 strategies), meeting protocols (3) |
| **memory** | Persistent agent memory: retrieval pipeline (ranking, filtering, injection), shared org memory, consolidation/archival |
| **security** | Security subsystem: SecOps agent, rule engine (soft-allow/hard-deny), output scanner, autonomy levels, timeout policies |
| **budget** | Cost management: cost tracking, budget enforcement (pre-flight/in-flight), quota/subscription, CFO optimiser, spending reports |
| **hr** | Agent lifecycle: hiring, firing, onboarding, offboarding, registry, performance tracking, promotion/demotion |
| **tools** | Tool system: registry, built-in tools (file system, git, sandbox, code runner), MCP bridge, role-based access |
| **api** | REST + WebSocket API: Litestar controllers, JWT + API key + WS ticket auth, guards, channels, RFC 9457 structured error responses |
| **config** | Company configuration: YAML schema, loader, validation, defaults |
| **templates** | Pre-built company templates: personality presets, template builder |
| **persistence** | Operational data: pluggable backend protocol with SQLite and Postgres implementations |
| **observability** | Structured logging: structlog, event constants, correlation tracking, log sinks |
| **meta** | Self-extending and self-improving organisation: MCP tool surface, charter, toolsmith, rollout mutators, Chief of Staff |
| **integrations** | External service connections: OAuth 2.1, webhooks, MCP catalog, rate limiting, tunnel provider |
| **`organization`** | Company org chart, department records, team management |
| **coordination** | Multi-agent coordination: ceremony policy plus coordination service and state |
| **research** | Research mode pipeline: query planning, multi-source retrieval, credibility triage, synthesis |
| **approval** | Approval workflow protocol, models, and decision routing |
| **notifications** | Operator alert adapters and notification dispatch |
| **settings** | Settings registry, runtime resolution (DB > env > default), and configuration precedence |
| **a2a** | Optional JSON-RPC 2.0 A2A federation domain: outbound client, peer registry, agent-card projection, HMAC webhook verification (disabled by default); the inbound gateway + well-known route handlers live under `api/a2a/` |
| **knowledge** | Knowledge and provenance substrate: document/knowledge RAG over an ingested external corpus |
| **ontology** | Semantic ontology subsystem: `OntologyService`, entity definitions and repository, drift detection |
| **project_brain** | Long-horizon per-project state store: decisions and rationale, open questions, structured queryable history |
| **docs_engine** | Living-documentation engine: per-project documentation store, human-browsable and agent-queryable |
| **backup** | Backup and restore: scheduled, lifecycle-triggered, and manual backups of the persistence DB, agent memory, and company configuration, with retention |
| **deliverable_receipts** | Immutable, self-validating provenance receipts attached to every completed deliverable (sources, decisions, rationale, cost) |
| **execution** | Execution-trajectory data models (per-turn record, trajectory enums, efficiency ratios), free of any `engine` dependency |
| **idempotency** | Claim/complete/fail idempotency service with response caching, shared by API controllers, the A2A gateway, and the MCP handler layer |
| **infrastructure** | Thin per-subdomain MCP facades backing the `synthorg_settings_*`, `synthorg_providers_*`, `synthorg_backup_*` tool families |
| **llm** | Cross-cutting LLM helpers: model pinning (`ModelPinMetadata`) and prompt-class profile metadata |
| **telemetry** | Opt-in, privacy-safe anonymous product telemetry (off by default; no API keys or chat content) |
| **versioning** | Generic versioning infrastructure for frozen Pydantic models (`VersioningService[T]`) |
| **workers** | Distributed task-queue workers that plug into `TaskEngine` via `register_observer`, preserving the single-writer mutation-queue invariant |

## Design Principles

1. **Protocol-driven**: every major subsystem defines a protocol interface. Concrete strategies implement the protocol. New strategies can be added without modifying existing code.

2. **Immutability**: configuration and identity use frozen Pydantic models. Runtime state evolves via `model_copy(update=...)`. No in-place mutation.

3. **Fail-closed security**: the security rule engine defaults to deny. Actions must be explicitly allowed by matching rules.

4. **Structured concurrency**: async operations use `asyncio.TaskGroup` for fan-out/fan-in. No bare `create_task` calls.

5. **Provider-agnostic**: all LLM interactions go through the provider abstraction. No vendor-specific code in business logic.

6. **Observable by default**: every module uses structured logging with domain-specific event constants. Correlation IDs track requests across agent boundaries.

## Further Reading

- [Design Specification](../design/index.md): full design spec split into multiple focused pages
- [Tech Stack](tech-stack.md): technology choices and engineering conventions
- [Decision Log](decisions.md): all design decisions, organised by domain
- [REST API](../openapi/index.md): REST + WebSocket API reference (Scalar/OpenAPI)
- [Library Reference](../api/index.md): auto-generated from source code
