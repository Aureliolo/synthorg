---
search:
  exclude: true
---

# Library Reference

Auto-generated reference documentation from source code docstrings.

This section documents every public class, function, and model in the SynthOrg framework. Documentation is generated automatically by [mkdocstrings](https://mkdocstrings.github.io/) using [Griffe](https://mkdocstrings.github.io/griffe/) for AST-based extraction.

## Modules

### Core Framework

| Module | Description |
|--------|-------------|
| [Core](core.md) | Shared domain models: Agent, Task, Role, Company, Project |
| [Engine](engine.md) | Agent orchestration, execution loops, task decomposition |
| [Providers](providers.md) | LLM provider abstraction, routing, resilience |
| [Config](config.md) | YAML company configuration loading and validation |

### Agent Capabilities

| Module | Description |
|--------|-------------|
| [Communication](communication.md) | Message bus, delegation, conflict resolution, meetings |
| [Memory](memory.md) | Persistent agent memory, retrieval pipeline, org memory |
| [Security](security.md) | Rule engine, trust, autonomy, output scanning |
| [Budget](budget.md) | Cost tracking, enforcement, optimisation |
| [HR](hr.md) | Agent lifecycle, performance tracking, promotions |
| [Tools](tools.md) | Tool registry, built-in tools, MCP bridge |

### Infrastructure

| Module | Description |
|--------|-------------|
| [API Layer](layer.md) | REST + WebSocket API, auth, controllers |
| [Persistence](persistence.md) | Pluggable storage backends |
| [Observability](observability.md) | Structured logging, events, correlation |
| [Templates](templates.md) | Pre-built company templates and presets |
