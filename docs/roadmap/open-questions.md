# Open Questions & Risks

## Open Questions

The following design questions remain unresolved. Each carries potential impact on architecture or behavior and will be addressed as the project progresses.

Numbers are stable identifiers — resolved questions are removed without renumbering to preserve cross-references.

| # | Question | Impact | Notes |
|---|----------|--------|-------|
| 1 | How deep should agent personality affect output? | Medium | Too deep leads to inconsistency; too shallow makes all agents feel the same. |
| 3 | How to handle context window limits for long tasks? | High | Agents may lose track of complex multi-file changes. |
| 4 | Should agents be able to create/modify other agents? | Medium | For example, a CTO "hires" a developer by creating a new agent config. |
| 6 | What metrics define "good" agent performance? | Medium | Needed for HR/hiring/firing decisions. |
| 8 | Optimal message bus for local-first architecture? | Medium | asyncio queues vs Redis vs embedded broker. |
| 10 | What is the minimum viable meeting set? | Low | Standup + planning + review as a starting point? |

---

## Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context window exhaustion on complex tasks | High | Memory summarization, task decomposition, working memory management. |
| Cost explosion from agent loops | High | Budget hard stops, loop detection, max iterations per task. |
| Agent quality degradation with cheap models | Medium | Quality gates, minimum model requirements per task type. |
| Third-party library breaking changes | Medium | Pin versions, integration tests, abstraction layers. |
| Memory retrieval quality | Medium | Mem0 selected as initial backend (ADR-001). Protocol layer enables backend swap if retrieval quality is insufficient. Pin version, test Python 3.14 compatibility in CI. |
| Agent personality inconsistency | Low | Strong system prompts, few-shot examples, personality tests. |
| WebSocket scaling | Low | Start local, add Redis pub/sub when needed. |

---

## Architecture Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Over-engineering the MVP | High | Start with a minimal viable company (3--5 agents), add complexity iteratively. |
| Config format becoming unwieldy | Medium | Good defaults, layered config (base + overrides), validation. |
| Agent execution bottlenecks | Medium | Async execution, parallel agent processing, queue-based architecture. |
| Data loss on crash | Medium | WAL mode SQLite. `RecoveryStrategy` protocol: fail-and-reassign implemented, checkpoint recovery planned. |
| Orchestration overhead exceeds productive work | Medium | LLM call analytics: proxy metrics implemented, call categorization and orchestration ratio alerts planned. |
