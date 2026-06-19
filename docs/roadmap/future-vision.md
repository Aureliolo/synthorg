# Future Vision

The longer-term direction for SynthOrg. Items here are either **planned** (scheduled or under active design) or **backlog** (candidates for research, not yet scheduled).

## Planned

| Feature | Status |
|---------|--------|
| Dynamic company scaling across clusters | Planned |

## Backlog (Research Candidates)

| Feature | Status |
|---------|--------|
| TimescaleDB hypertable support for append-only time-series tables | Research |
| Community template marketplace | Research |
| Inter-company communication beyond A2A | Research |
| Shift system for agents | Research |
| Self-improving company (meta-loop signal aggregation, staged rollout) | Research |
| Advanced memory architecture (GraphRAG, consistency protocols, RL consolidation) | Research |
| Kubernetes sandbox backend | Research |
| Training mode (learn from senior agents) | Research |
| Agent-controlled context compaction (agent-guided compaction tool, LLM summarisation, memory offload) | Research |

## Scaling Path

SynthOrg is designed to scale incrementally from a local single-process deployment to a hosted platform.

```mermaid
flowchart LR
    P1["Local single-process<br/>async runtime, SQLite,<br/>in-memory bus, 1-10 agents"]
    P2["Local multi-process<br/>external bus, production DB,<br/>sandboxed execution, 10-30 agents"]
    P3["Network / server<br/>distributed agents,<br/>multi-user auth, 30-100 agents"]
    P4["Cloud / hosted<br/>container orchestration,<br/>horizontal scaling, 100+ agents"]

    P1 --> P2 --> P3 --> P4

    P1:::shipped
    P2:::shipped
    P3:::planned
    P4:::backlog

    classDef shipped fill:#1e3a2e,stroke:#22c55e,color:#e2e8f0
    classDef planned fill:#3a2e1e,stroke:#f59e0b,color:#e2e8f0
    classDef backlog fill:#2e2e3a,stroke:#8b5cf6,color:#e2e8f0
```

See the [Distributed Runtime](../design/distributed-runtime.md) page for the NATS JetStream backend and distributed task queue design.

Each phase builds on the previous one. The pluggable protocol interfaces throughout the codebase (persistence, memory, message bus, sandbox) are designed to make these transitions configuration changes rather than rewrites.
