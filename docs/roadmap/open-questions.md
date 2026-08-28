# Open Questions & Risks

## Open Questions

The following design questions remain unresolved. Each carries potential impact on architecture or behaviour and will be addressed as the project progresses.

Numbers are stable identifiers; resolved questions are removed without renumbering to preserve cross-references.

| # | Question | Impact | Notes |
|---|----------|--------|-------|
| 4 | Should agents be able to create/modify other agents? | Medium | For example, a CTO "hires" a developer by creating a new agent config. Partly settled by construction: the agent MCP surface carries `agents:create` / `agents:update` / `agents:delete`, all admin-guardrailed, and none of them may grant a gate role, because a role that judges finished work would let an agent staff its own reviewer. The open half is whether the guardrail should be the ceiling. |
| 6 | What metrics define "good" agent performance? | Medium | Quality is the completion oracle's verdict per task; the tracker adds reliability, cost, and latency over rolling windows. Uncalibrated against live evidence: completion reviews have run in live rounds, but the verdicts they produced were decided by a defect (the review read its deliverable from a store written after it had already ruled), so there is no corpus of verdicts to tune against. |

---

## Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| A per-node decomposition bound takes down a converged tree | High | The dominant risk, and the one live rounds keep landing on. Three separate bounds have each discarded a whole tree: a wall-clock ceiling, turn exhaustion, and a width cap the atomicity gate cannot read. The first two are absorbed at the level that asked for the split, so a node fails without the tree failing. The third is open: the two caps that size a level contradict each other, so compliance was fatal and non-compliance was rejected. See [the loop round log](../reference/loop-round-log.md). |
| A run dies with rows nothing will move again | High | Level-triggered recovery rather than edge-triggered dispatch: `RunRecoveryReconciler` classifies every plan status on every pass, requeues orphaned rows as `INTERRUPTED`, re-judges a task left in review with no open human decision, and hands the waves back to the coordinator. `gate_wave` parks a subtask whose declared inputs died, and the three abandon passes cover the waves a stopped run never reached, the wave that raised, and the rows routing could place in no wave at all. |
| Context window exhaustion on complex tasks | Medium | **Partially mitigated**: context budget management tracks fill, injects indicators, and compacts at turn boundaries. Remaining: LLM-based summarization for higher-quality summaries. |
| Cost explosion from agent loops | High | Budget hard stops, loop detection, max iterations per task, and a capability ladder that picks the band first (the exact rung, else the nearest higher, else the nearest lower) and orders on cost only within that band, so it buys the cheapest agent at the rung the work demands rather than the cheapest agent that could scrape through. Nothing re-points a bound `(provider, model)` pair to save money; an agent is a fixed unit and work needing more capability goes to a different agent. |
| Agent quality degradation with cheap models | Medium | Capability-aware prompt profiles adapt prompts to model capability. Quality gates and minimum model requirements per task type. |
| Third-party library breaking changes | Medium | Python deps exact-pinned (`==`), JS deps range-based with lockfiles. Integration tests, abstraction layers, Renovate weekly updates. |
| Memory retrieval quality | Medium | Hybrid retrieval (dense + BM25 sparse with RRF fusion) shipped. LMEB-guided embedding selection implemented. The domain fine-tuning orchestrator is wired into boot; trajectory-mode training additionally requires a configured memory backend, otherwise the controllers degrade to HTTP 501. |
| Agent output inconsistency | Low | One org-wide house writing style with per-role and per-department scoping, hard rules enforced deterministically at the boundary. |
| WebSocket scaling | Low | In-process channels today. Multi-instance fan-out can ride on the shipped NATS JetStream bus when needed. |

---

## Architecture Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Over-engineering the MVP | High | Start with a minimal viable company (3-5 agents), add complexity iteratively. 12 company templates provide tested starting points. |
| Config format becoming unwieldy | Medium | Good defaults, layered config (base + overrides), validation via Pydantic v2 models, setup wizard for guided configuration. |
| Agent execution bottlenecks | Medium | Async execution, parallel agent processing, queue-based architecture. TaskGroup for structured concurrency. |
| Data loss on crash | Medium | WAL mode SQLite, checkpoint recovery, backup/restore with scheduled retention. A restart also re-drives in-flight runs rather than stranding them: see the recovery row above. |
| Orchestration overhead exceeds productive work | Medium | LLM call analytics with proxy metrics, call categorisation, and orchestration-ratio alerting are all implemented. Unmeasured on a finished run, because no live round has produced one. |
| SQLite contention under concurrent access | Low | Single-writer with WAL mode handles read concurrency well. The PostgreSQL backend (conformance-tested for parity) handles write-heavy and multi-instance workloads. |
