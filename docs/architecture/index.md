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
| **engine** | Agent orchestration: execution loops (ReAct, Plan-and-Execute), task decomposition, routing, assignment, parallel execution, recovery, shutdown |
| **engine.intake** | Intake processing: ClientRequest lifecycle, IntakeStrategy protocol, validation |
| **engine.review** | Review pipeline: ReviewStage protocol, multi-stage review orchestration, verdict tracking |
| **providers** | LLM provider abstraction: LiteLLM adapter, capability matching, routing strategies (5), retry + rate limiting |
| **communication** | Inter-agent messaging: bus, dispatcher, delegation, loop prevention, conflict resolution (4 strategies), meeting protocols (3) |
| **memory** | Persistent agent memory: retrieval pipeline (ranking, filtering, injection), shared org memory, consolidation/archival |
| **security** | Security subsystem: SecOps agent, rule engine (soft-allow/hard-deny), output scanner, progressive trust (4 strategies), autonomy levels, timeout policies |
| **budget** | Cost management: cost tracking, budget enforcement (pre-flight/in-flight), auto-downgrade, quota/subscription, CFO optimiser, spending reports |
| **hr** | Agent lifecycle: hiring, firing, onboarding, offboarding, registry, performance tracking, promotion/demotion |
| **tools** | Tool system: registry, built-in tools (file system, git, sandbox, code runner), MCP bridge, role-based access |
| **api** | REST + WebSocket API: Litestar controllers, JWT + API key + WS ticket auth, guards, channels, RFC 9457 structured error responses |
| **config** | Company configuration: YAML schema, loader, validation, defaults |
| **templates** | Pre-built company templates: personality presets, template builder |
| **persistence** | Operational data: pluggable backend protocol, SQLite implementation |
| **observability** | Structured logging: structlog, event constants, correlation tracking, log sinks |

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
