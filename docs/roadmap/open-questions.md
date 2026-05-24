# Open Questions & Risks

## Open Questions

The following design questions remain unresolved. Each carries potential impact on architecture or behaviour and will be addressed as the project progresses.

Numbers are stable identifiers; resolved questions are removed without renumbering to preserve cross-references.

| # | Question | Impact | Notes |
|---|----------|--------|-------|
| 1 | How deep should agent personality affect output? | Medium | Too deep leads to inconsistency; too shallow makes all agents feel the same. Capability-aware prompt profiles (#805) will add tier-based personality condensation. |
| 4 | Should agents be able to create/modify other agents? | Medium | For example, a CTO "hires" a developer by creating a new agent config. |
| 6 | What metrics define "good" agent performance? | Medium | Five-pillar evaluation framework (#1017) provides structure; quality scoring Layers 2+3 (#230) will add LLM judge and human override. |
| 8 | Optimal message bus for local-first architecture? | Medium | asyncio queues for single-process; NATS JetStream shipped for distributed deployments. |

---

## Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context window exhaustion on complex tasks | Medium | **Partially mitigated**: context budget management tracks fill, injects indicators, and compacts at turn boundaries. Remaining: LLM-based summarization for higher-quality summaries. |
| Cost explosion from agent loops | High | Budget hard stops, loop detection, max iterations per task, auto-downgrade at task boundaries. |
| Agent quality degradation with cheap models | Medium | Capability-aware prompt profiles (#805) adapt prompts to model tier. Quality gates and minimum model requirements per task type. |
| Third-party library breaking changes | Medium | Python deps exact-pinned (`==`), JS deps range-based with lockfiles. Integration tests, abstraction layers, Renovate daily updates. |
| Memory retrieval quality | Medium | Hybrid retrieval (dense + BM25 sparse with RRF fusion) shipped. LMEB-guided embedding selection implemented. Domain fine-tuning pipeline not yet implemented; config and checkpoint lookup wired, training stages raise `NotImplementedError` (#1001). |
| Agent personality inconsistency | Low | Strong system prompts, personality presets with condensed/minimal variants planned (#805). |
| WebSocket scaling | Low | In-process channels today. Multi-instance fan-out can ride on the shipped NATS JetStream bus when needed. |

---

## Architecture Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Over-engineering the MVP | High | Start with a minimal viable company (3-5 agents), add complexity iteratively. 9 company templates provide tested starting points. |
| Config format becoming unwieldy | Medium | Good defaults, layered config (base + overrides), validation via Pydantic v2 models, setup wizard for guided configuration. |
| Agent execution bottlenecks | Medium | Async execution, parallel agent processing, queue-based architecture. TaskGroup for structured concurrency. |
| Data loss on crash | Medium | WAL mode SQLite, checkpoint recovery, backup/restore with scheduled retention. |
| Orchestration overhead exceeds productive work | Medium | LLM call analytics with proxy metrics implemented. Call categorisation and orchestration ratio alerts planned. |
| SQLite contention under concurrent access | Low | Single-writer with WAL mode handles read concurrency well. PostgreSQL backend planned for write-heavy workloads. |
