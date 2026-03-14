# Roadmap

## Current Status

The SynthOrg core framework is complete. The following subsystems are built and tested:

- Provider abstraction layer (LiteLLM adapter, routing, resilience)
- Budget and cost management (tracking, enforcement, CFO optimization, quotas)
- Agent engine (execution loops, parallel execution, task decomposition, routing, assignment, recovery, shutdown, multi-agent coordination)
- Communication layer (message bus, delegation, loop prevention, conflict resolution, meeting protocol)
- Memory system (pluggable backend protocol, Mem0 adapter, retrieval pipeline, shared org memory, consolidation)
- Security and approval system (rule engine, output scanning, progressive trust, autonomy levels, timeout policies)
- Tool system (file system, git, code runner, MCP bridge, sandboxing, permissions)
- HR engine (hiring, firing, onboarding, offboarding, registry, performance tracking, promotions)
- REST and WebSocket API (Litestar controllers, JWT + API key auth, WebSocket channels)
- Persistence layer (pluggable protocol, SQLite backend, repository protocols)
- Observability (structured logging, correlation tracking, per-domain event constants)
- Configuration (YAML loading, Pydantic validation, company templates with inheritance)
- Container packaging (Docker, Chainguard distroless, CI/CD pipelines)

## In Progress

| Area | Description |
|------|-------------|
| **Web dashboard** | Vue 3 + PrimeVue + Tailwind CSS frontend for monitoring and managing the synthetic organization (core infrastructure merged, page views pending) |

## Remaining Work

| Area | Description |
|------|-------------|
| **Approval workflow gates** | Runtime wiring for human-in-the-loop approval queues |
| **CLI** | Terminal interface wrapping the REST API (may not be needed) |

## Tracking

Implementation issues are tracked on the [GitHub issue tracker](https://github.com/Aureliolo/synthorg/issues) and prioritized by dependency order.

## Further Reading

- [Open Questions & Risks](open-questions.md) -- unresolved design questions and identified risks
- [Future Vision](future-vision.md) -- post-MVP features and the scaling path
