# ADR-002: Design Decisions Batch 1 (D1–D23)

> **Status:** DECIDED (2026-03-09)
> **Generated:** 2026-03-09 by 11 parallel research agents (one per issue group, plus one cross-cutting coordinator).
> **Decided:** 2026-03-09 — all 23 decisions finalized by user.
>
> Each decision includes options, pros/cons, real-world precedents, and the chosen approach.

---

## Overarching Pattern

**Nearly every decision follows the same architecture:** a pluggable protocol interface with one initial implementation shipped, and alternative strategies documented in DESIGN_SPEC.md for future. This is consistent with the project's protocol-everywhere design philosophy.

---

## Cross-Cutting Decisions (D1–D3)

### D1: Action Type Taxonomy

**Unblocks:** #40, #42, #126

**Context:** The autonomy presets reference action types informally (code_changes, tests, docs, deployment, hiring, etc.) but there's no formal enum, no definition of what each covers, and no registry. These action types are used by autonomy presets, SecOps validation, tiered timeout policies, and progressive trust.

**Sub-question 1 (D1.1): Fixed enum vs open/extensible registry?**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Closed enum** | Type safety, autocomplete, typos caught at compile time | Cannot extend for custom company templates; violates "Configuration over Code" principle |
| **(b) Open string** | Unlimited extensibility | Typos silently accepted — security hazard in approval system (typo = skip approval); no discoverability |
| **(c) Enum core + validated registry (CHOSEN)** | Built-in types have type safety + autocomplete; custom types supported via explicit registration; typos caught at config validation time | Slightly more complex than pure enum |

**Precedents:** AWS IAM uses open namespaced strings (`s3:GetObject`). Kubernetes RBAC uses semi-open verbs. GitHub uses closed scopes. OPA/Rego uses open policy strings. Every production security system validates action strings against a known set.

**Decision:** **(c) Enum core + validated registry.** StrEnum for built-in types (~25), plus an `ActionTypeRegistry` that accepts custom strings only if explicitly registered. Unknown strings rejected at config load time. Critical for security — a typo in `human_approval` list silently means "skip approval."

**Sub-question 2 (D1.2): Granularity — two-level hierarchy?**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Flat list (~15 types)** | Simple config | Can't distinguish file_edit from file_create (supervised preset needs this) |
| **(b) Two-level hierarchy `category:action` (CHOSEN)** | Simple config via category shortcuts (`auto_approve: ["code"]`) AND fine-grained control (`human_approval: ["code:create"]`); matches AWS/GCP pattern | Slightly more complex parsing |
| **(c) Three+ levels** | Maximum granularity | Overkill; no one gates by language or sub-sub-type |

**Proposed taxonomy (~25 leaf types):**

```text
code:read, code:write, code:create, code:delete, code:refactor
test:write, test:run
docs:write
vcs:commit, vcs:push, vcs:branch
deploy:staging, deploy:production
comms:internal, comms:external
budget:spend, budget:exceed
org:hire, org:fire, org:promote
db:query, db:mutate, db:admin
arch:decide
```

**Decision:** **(b) Two-level `category:action` hierarchy** with category shortcuts. `auto_approve: ["code"]` expands to all code:* actions. Keeps simple configs simple, power configs powerful.

**Sub-question 3 (D1.3): Who classifies an action into a type?**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Static tool metadata (CHOSEN)** | Deterministic, zero overhead, predictable for users; matches AWS/K8s/GCP pattern | Cannot consider arguments (writing to /deploy/ vs /src/) |
| **(b) Runtime pattern matching** | Argument-aware (file path determines type) | Complex pattern maintenance; still deterministic |
| **(c) LLM classification** | Handles novel tools | Non-deterministic — catastrophic for security; adds latency + cost; vulnerable to prompt injection |
| **(d) Static primary + optional enrichment** | Best of (a) + (b) | Slightly more complex |

**Decision:** **(a) Static tool metadata primary**, with optional deterministic enrichment layer for advanced use. Each `BaseTool` declares its `action_type`. Default mapping from `ToolCategory` → action type. Non-tool action types (org:hire, budget:spend) triggered by engine-level operations. No LLM in the security classification path.

---

### D2: Quality Scoring Mechanism

**Unblocks:** #47, #43, #49

**Context:** The spec says `average_quality_score: 8.5` — "from code reviews, peer feedback" — but defines no mechanism. This score gates trust promotions (`quality_score_min: 7.0`) and hiring/firing decisions.

| Option | Pros | Cons | Cost |
|--------|------|------|------|
| **(a) Human inputs via API** | Highest fidelity, no gaming | Doesn't scale; bottleneck for promotions | Human time |
| **(b) LLM-as-judge** | Scales to any throughput; consistent rubric; captures qualitative dimensions | 12+ known biases (verbosity, self-enhancement, position); costs tokens | ~$1-5/day |
| **(c) Automated objective signals** | Zero token cost; completely objective (test pass rate, lint, coverage) | Only works for code tasks; narrow quality view; gameable | Free |
| **(d) Peer agent ratings** | Captures collaboration dimensions | Reciprocity bias, collusion, strategic manipulation; LLMs rating LLMs is conceptually suspect | Minimal |
| **(e) Combination: objective baseline + LLM judge + human override (CHOSEN)** | Multiple independent signals; hardest to game; scales with human oversight for edge cases | Most complex to implement | ~$1-5/day |

**Research highlights:**
- LLM judges align with human preferences >80% of time but exhibit 12+ biases (CALM framework)
- SWE-bench uses pure test-pass evaluation (option c) successfully
- LangSmith uses combination: automated LLM-as-judge + human annotation queues
- Peer ratings show severe reciprocity bias even in human systems (Caltech research)

**Decision:** **(e) Combination** — three layers:
1. **Layer 1 (free):** Objective CI signals — test pass/fail, lint errors, coverage delta → `objective_quality` sub-score
2. **Layer 2 (~$1/day):** Small-model LLM judge (different model family than agent) evaluates output against acceptance criteria → `assessed_quality` sub-score
3. **Layer 3 (on-demand):** Human override via REST API, highest weight when present

Start with Layer 1 only (free, sufficient for initial trust gates). Add layers incrementally.

---

### D3: Collaboration Scoring Mechanism

**Unblocks:** #47, #43, #49

**Context:** The spec says `collaboration_score: 7.8` — "peer ratings" — but agents don't currently have a mechanism to rate each other.

| Option | Pros | Cons | Cost |
|--------|------|------|------|
| **(a) Automated from communication patterns (CHOSEN)** | Completely objective; zero token cost; derived from existing telemetry (delegation success, response latency, conflict outcomes, meeting participation, loop triggers) | Measures behavior, not quality; context-dependent | Free |
| **(b) LLM evaluation of message quality** | Captures nuanced helpfulness | Expensive at scale (thousands of messages); circular (LLM judging LLM communication to LLM) | High |
| **(c) Peer agent ratings** | Captures firsthand interaction quality | Same reciprocity/collusion problems as D2(d); LLMs have no genuine opinions | Minimal |
| **(d) Human-provided periodically** | Highest fidelity; cannot be gamed | Doesn't scale; too infrequent for real-time decisions | Human time |

**Decision:** **(a) Automated behavioral telemetry** as primary signal:

```text
collaboration_score = weighted_average(
    delegation_success_rate,
    delegation_response_latency,
    conflict_resolution_constructiveness,
    meeting_contribution_rate,
    loop_prevention_score,         # penalty for causing loops
    handoff_completeness,
)
```

Weights configurable per-role. Optional: periodic LLM sampling (1% of interactions) for calibration. Human override via REST API.

---

## SecOps Decisions (D4–D5)

### D4: SecOps — LLM-based or Rule-based?

**Unblocks:** #40

| Option | Pros | Cons | Latency |
|--------|------|------|---------|
| **(a) Pure rule engine** | Fast, deterministic, zero LLM cost; catches 80-90% of predictable threats (credentials, path traversal, destructive ops) | Can't handle novel situations or semantic reasoning | Sub-ms |
| **(b) Pure LLM agent** | Flexible, reasons about novel actions and intent | 0.5-8.6s per evaluation; non-deterministic; costs tokens on every action; itself vulnerable to prompt injection | 0.5-8.6s |
| **(c) Hybrid: rule engine fast path + LLM slow path (CHOSEN)** | Rules catch known patterns deterministically; LLM handles uncertain cases; rules serve as backstop if LLM fails | Two systems to maintain; handoff logic needs tuning | Sub-ms (est. 95%), 0.5-2s (est. 5%) |

**Precedents:** AWS GuardDuty (YARA rules + ML anomaly detection), LlamaFirewall (PromptGuard + AlignmentCheck + CodeShield), Google ADK (in-tool guardrails + callback hooks), NeMo Guardrails (Colang DSL + LLM classification). **Every production security system uses a hybrid approach.**

**Sub-decision: SecOps in "full autonomy" mode?**
- **Always run rules + audit logging** regardless of autonomy level (even root has auditd)
- LLM slow path and human escalation disabled in full mode
- Hard safety rules (credential exposure, data destruction) never bypass

**Decision:** **(c) Hybrid.** Rule engine for known patterns (sub-ms). LLM fallback only for uncertain cases (estimated ~5% of actions). Full autonomy mode: rules + audit only, no LLM path.

---

### D5: SecOps — Integration Point in Pipeline

**Unblocks:** #40

| Option | Pros | Cons |
|--------|------|------|
| **(a) Before every tool invocation (CHOSEN)** | Maximum security coverage; catches per-action threats; Google ADK, LlamaFirewall, Snyk all use this | Highest number of checks (but sub-ms with rule engine) |
| **(b) Before task step execution (batch level)** | Can see tool combinations; fewer checks | Cannot stop individual tools mid-batch; misses threats within batches |
| **(c) Before task assignment only** | Minimal overhead | Zero runtime security; just access control (already have ToolPermissionChecker) |
| **(d) Configurable per autonomy level** | Maximum flexibility | The interception POINT doesn't actually change — only the POLICY strictness does |

**Performance reality:** Our bottleneck is LLM inference (seconds). A sub-ms rule check per tool call is invisible. Even OPA sidecar evaluations are 1-5ms. Total security overhead: milliseconds against minutes of LLM time.

**Decision:** **(a) Before every tool invocation**, with policy strictness (not interception point) configurable per autonomy level. Implement behind a pluggable `SecurityInterceptionStrategy` protocol. Slots naturally into existing `ToolInvoker` between permission check and tool execution. Add post-tool-call checking for result scanning (detect sensitive data in outputs).

---

## Autonomy Decisions (D6–D7)

### D6: Autonomy — Per-Agent or Company-Wide?

**Unblocks:** #42

| Option | Pros | Cons |
|--------|------|------|
| **(a) Company-wide only** | Simplest; no misconfiguration risk | Too coarse; contradicts existing seniority system; can't give trusted CEO more freedom than new intern |
| **(b) Per-agent override with company default (CHOSEN)** | Matches every real-world IAM system (AWS, Azure, K8s); all AI frameworks use per-agent (CrewAI, AutoGen, LangGraph); aligns with existing per-agent seniority instructions and tool access | Risk of misconfiguration (mitigated by seniority-based validation rules) |
| **(c) Per-department** | Middle ground | Still can't distinguish junior from lead within same department; no real-world system stops at group-only |

**Precedents:** CrewAI has 24 per-agent attributes. AutoGen has per-agent `human_input_mode`. LangGraph has per-node `interrupt_before`/`interrupt_after`. CSA Agentic Trust Framework requires per-agent identity and trust level.

**Decision:** **(b) Per-agent override.** Optional `autonomy_level` on `AgentIdentity` and department config (default: None = use next level's default). Resolution: `agent.autonomy_level or department.autonomy_level or company.autonomy.level`. Add seniority-based validation (Juniors/Interns cannot be set to `full`).

---

### D7: Autonomy — Who Can Change Levels at Runtime?

**Unblocks:** #42

| Option | Pros | Cons |
|--------|------|------|
| **(a) Human only** | Most secure; all changes auditable; no privilege escalation risk | Potentially too restrictive (but API provides instant control) |
| **(b) Human + CEO agent** | Fits company metaphor | Severe security risk: prompt injection → CEO manipulated into escalating; cascading privilege escalation; accountability gap; unprecedented in any AI framework |
| **(c) Automatic based on conditions** | Adapts to context (error rate, budget, time) | Automatic PROMOTION is dangerous and unprecedented; automatic RESTRICTION is safe and well-precedented |
| **(a+c hybrid) Human-only promotion + automatic downgrade (CHOSEN)** | Asymmetric trust: gaining trust is hard, losing it is easy; matches Azure Conditional Access (only restricts, never loosens) | Two code paths |

**Key insight:** No real-world security system automatically grants higher privileges. Conditional access only steps UP requirements, never DOWN. The SEAgent MAC framework explicitly prevents agents from self-modifying policies.

**Decision:** **(a+c hybrid)** Human-only for promotion. Automatic downgrade on: high error rate → downgrade one level, budget exhausted → supervised, security incident → locked. Recovery from auto-downgrade: human-only.

---

## HR Decisions (D8–D10)

### D8: HR — Runtime Agent Instantiation

**Unblocks:** #45

**Sub-decision 1 (D8.1): Source**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Templates only** | Predictable, validated, reuses existing template system | Can't create novel roles |
| **(b) LLM-generated only** | Maximum flexibility for novel roles | Risk of invalid configs; non-deterministic |
| **(c) Both: template primary + LLM customization (CHOSEN)** | Templates for common cases; LLM customization for gaps; approval gate catches bad configs | Slightly more complex API surface |

**Sub-decision 2 (D8.2): Persistence**

| Option | Pros | Cons |
|--------|------|------|
| **(a) In-memory only** | Simplest | Lost on restart; can't rehire; can't audit |
| **(b) Persist to YAML** | Config is source of truth | YAML mutation at runtime is error-prone; race conditions |
| **(c) Operational store via PersistenceBackend (CHOSEN)** | Survives restart; auditable; enables rehiring; YAML stays as bootstrap seed | Need reconciliation strategy (operational store wins for runtime) |

**Sub-decision 3 (D8.3): Hot-plug**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Restart required** | Simplest | Unacceptable for running company; all agents stop |
| **(b) Hot-pluggable (CHOSEN)** | Matches company metaphor; enables auto-scaling; async architecture supports it | Need thread-safe registry; wire into message bus, tools, budget |

**Precedents:** AutoGen is hot-pluggable by design (`register()` at any time). Letta persists everything to database. No serious framework requires restart for agent changes.

**Decision:** **(c) Both sources**, **(c) operational store**, **(b) hot-pluggable**. Template-based MVP. `HiringRequest` model carries template reference + overrides or custom config. Operational store via existing `PersistenceBackend`. Hot-plug via dedicated company/registry service (not `AgentEngine`, which remains the per-agent task runner).

---

### D9: HR — Task Reassignment on Offboarding

**Unblocks:** #45

| Option | Pros | Cons |
|--------|------|------|
| **(a) Same-department, lowest load** | Fast, automatic, no LLM calls | Ignores skill match |
| **(b) Manager decides** | Matches real-world practice | LLM cost per task; blocks on manager availability |
| **(c) HR agent decides (LLM matching)** | Best skill-task matching | Most expensive; HR becomes bottleneck |
| **(d) Tasks return to unassigned queue** | Simplest; zero coupling; existing TaskRoutingService handles re-routing | Risk of orphaned tasks if queue processing slow |
| **(e) Configurable `TaskReassignmentStrategy` protocol (CHOSEN)** | Matches project's protocol-everywhere pattern; different strategies for different situations | More code to write |

**Decision:** **(e) Configurable protocol** with **(d) queue-return** as default MVP. Existing `TaskRoutingService` + `AgentTaskScorer` already handle skill-based routing. Add priority boost for reassigned tasks. Manager-decides as first non-trivial strategy upgrade.

---

### D10: HR — Memory Archival Semantics

**Unblocks:** #45

| Option | Pros | Cons |
|--------|------|------|
| **(a) Full snapshot, accessible** | Complete preservation; enables forensic analysis | Storage grows; personal reasoning exposed to others |
| **(b) Selective: org-relevant promoted, personal discarded** | Clean; high-quality org memory | "Org-relevant" requires classification (LLM cost); irrecoverable if wrong |
| **(c) Full snapshot, read-only, restorable (CHOSEN)** | Everything preserved but frozen; enables rehiring; selective promotion as separate non-destructive step; existing `ArchivalStore` protocol supports it directly | Stores everything (but storage is cheap) |

**Decision:** **(c) Full snapshot, read-only.** Pipeline: retrieve all → archive to `ArchivalStore` → selectively promote semantic+procedural to `OrgMemoryBackend` (rule-based auto) → clean hot store → mark TERMINATED. Rehiring = restore archived memories into new `AgentIdentity`.

---

## Performance Metrics (D11–D12)

### D11: Rolling Average Window

**Unblocks:** #47

| Option | Pros | Cons |
|--------|------|------|
| **(a) Fixed 30 days** | Simplest | Too rigid for heterogeneous metrics; cost can shift in hours, quality arrives weekly |
| **(b) Configurable per metric** | Flexibility | Adds config complexity; still single-resolution per metric |
| **(c) Multiple windows: 7d, 30d, 90d (CHOSEN)** | Industry standard (Google SRE, Prometheus, Datadog); handles heterogeneous cadences; sparse data resilience (fallback to longer windows); enables multi-window alerting | 3x computation (negligible at agent scale) |

**Key evidence:** Google SRE Workbook prescribes multi-window, multi-burn-rate alerting as "the most appropriate approach." Every major monitoring platform uses this.

**Decision:** **(c) Multiple windows** — 7d (acute regressions), 30d (sustained patterns), 90d (baseline/drift). Minimum 5 data points per window; below that, report "insufficient data."

---

### D12: Trend Detection Approach

**Unblocks:** #47

| Option | Pros | Cons |
|--------|------|------|
| **(a) Period-over-period comparison** | Simplest (O(1) after averages) | Statistically weak; sensitive to window boundaries; no significance measure; high false positive rate |
| **(b) Linear regression slope** | Statistically principled; gives direction + magnitude + significance | Assumes linear trend; OLS has 0% outlier breakdown |
| **(c) Threshold-based flagging** | Filters noise into actionable categories | Not a trend detection method — only answers "crossed boundary?" not "trending?" |
| **(b+c hybrid) Theil-Sen regression + thresholds (CHOSEN)** | Theil-Sen: 29.3% outlier breakdown (tolerates ~1 in 3 bad points); thresholds filter noise into improving/stable/declining; minimum data point guard | Slightly more complex than simple comparison |

**Key evidence:** Theil-Sen estimator is 91% as efficient as OLS on normal data but dramatically better on heavy-tailed data. EPA recommends it for environmental trend detection. Perfect for agent metrics with occasional catastrophic task failures.

**Decision:** **(b+c hybrid)** — Theil-Sen slope per window, thresholds per metric to classify as improving/stable/declining. Minimum 5 data points per window.

---

## Promotion Decisions (D13–D15)

### D13: Promotion Criteria Logic (AND/OR)

**Unblocks:** #49

| Option | Pros | Cons |
|--------|------|------|
| **(a) All (AND)** | Strictest; prevents gaming via one strong metric | Agent with quality=9.5 but collaboration=6.9 blocked forever from junior→mid |
| **(b) Any (OR)** | Most lenient | Agent completing 100 trivial tasks auto-promotes to senior |
| **(c) Configurable per level: threshold gates (CHOSEN)** | Lower levels: lenient (2 of 3 criteria). Higher levels: strict (all). Single `ThresholdEvaluator` covers AND/OR/threshold | More configuration |

**Precedents:** Game progression systems predominantly use threshold gates ("complete any 3 of 5 challenges"). HR competency matrices use weighted composite scoring with per-dimension minimums.

**Decision:** **(c) Configurable per level.** `ThresholdEvaluator` with `min_criteria_met: int` + `required_criteria: list[str]`. Setting min=total gives AND. Setting min=1 gives OR. Default: junior→mid = 2 of 3; mid→senior = all.

---

### D14: Promotion Approval Requirements

**Unblocks:** #49

| Option | Pros | Cons |
|--------|------|------|
| **(a) All promotions human-approved** | Safest for budget | Bottleneck; queue floods on mass promotion events |
| **(b) Only senior+ requires human (CHOSEN)** | Low levels auto-promote (small budget impact: small→medium ~4x); high levels human-gated (large budget impact: medium→large ~5-10x) | Accidental auto-promotion possible for junior/mid |
| **(c) Configurable per level** | Maximum flexibility | Extra config complexity without clear benefit over (b) |

**Additional:** Demotions should auto-apply for cost-saving (model downgrade) but require human approval for authority-reducing demotions.

**Decision:** **(b) Senior+ requires human.** Mirrors industry graduated-autonomy patterns (CSA, Anthropic, AWS). Junior→mid is low-risk/low-cost. The existing `standard_to_elevated` tool access invariant already establishes this pattern.

---

### D15: Promotion — Seniority-to-Model Mapping

**Unblocks:** #49

| Option | Pros | Cons |
|--------|------|------|
| **(a) Always applied (promotion auto-changes model)** | Simple, predictable | Budget-constrained deployments can't promote for authority without cost increase |
| **(b) Opt-in (promotion = seniority only, model unchanged)** | Budget-friendly | Seniority system feels disconnected from agent capability |
| **(c) Default ON, configurable opt-out (CHOSEN)** | Existing `SeniorityInfo.typical_model_tier` already implemented; model changes at task boundaries (consistent with auto-downgrade §10.4); per-agent overrides take priority; `smart` routing cascade still routes simple tasks to cheap models | One more config flag |

**Current catalog mapping:** Junior→small, Mid→medium, Senior→medium, Lead+→large. The big cost jump is at Lead, not Senior — budget-conservative by design.

**Decision:** **(c) Default ON, configurable.** `hr.promotions.model_follows_seniority: true` (default). Model changes at task boundaries only (never mid-execution). Per-agent `preferred_model` overrides seniority default. Smart routing still uses cheap models for simple tasks regardless of seniority.

---

## Sandbox Decision (D16)

### D16: Sandbox Backend Choice

**Unblocks:** #50

**Main decision:**

| Option | Pros | Cons |
|--------|------|------|
| **(a) Docker only** | Simplest; covers all use cases; widest familiarity | 1-2s cold start (mitigatable) |
| **(b) Docker + WASM optional** | WASM gives microsecond starts | CPython-in-WASM can't run pip packages or C extensions — disqualifying |
| **(c) Docker + Firecracker optional** | Strongest isolation (hardware VM) | Linux-only (requires KVM); not available on macOS/Windows; complex setup; overkill for single-tenant |
| **(d) Docker MVP, evaluate later (CHOSEN)** | Ships minimum viable sandbox; `SandboxBackend` protocol makes adding backends trivial later; gVisor upgrade is config-level only | Defers optimization |

**Key performance insight:** LLM calls take 2-30s. Docker cold start (1-2s, sub-second with `--network none` + warm pool) is invisible in agent execution flow.

**Sub-decision 1: Docker image**
- **Pre-built default** (Python 3.14 + Node.js LTS + basic utils) + **user-configurable** via `docker.image` config
- Keep under 500MB; users add Go/Rust via custom images

**Sub-decision 2: Python library**
- **aiodocker (CHOSEN)** — async-native (matches our stack), explicit Python 3.14 support, aio-libs ecosystem, sufficient API coverage
- docker-py — sync (requires `asyncio.to_thread()` wrapping), no declared 3.14 support, sluggish maintenance

**Sub-decision 3: Docker unavailable fallback**
- **Fail with clear error (CHOSEN)** — no subprocess fallback for code execution (security anti-pattern)
- File/git tools already use SubprocessSandbox (no Docker needed)
- Industry consensus: E2B, OpenAI, Daytona — none offer unsandboxed fallback

**Decision:** **(d) Docker MVP.** aiodocker library. Pre-built image + user config. Fail if Docker unavailable. gVisor (`--runtime=runsc`) as free config-level hardening upgrade. Firecracker belongs in future K8s path.

---

## MCP Decisions (D17–D18)

### D17: MCP SDK Choice

**Unblocks:** #53

| Option | Pros | Cons |
|--------|------|------|
| **(a) Official `mcp` Python SDK (CHOSEN)** | Every major framework uses it (LangChain, CrewAI, OpenAI Agents, Pydantic AI); Python 3.14 compatible (tested, build issue resolved); Pydantic 2.12.5 compatible; all transports (stdio, Streamable HTTP); dependency overlap with Litestar stack | v2 migration upcoming (pin to `>=1.25,<2`); beta classification |
| **(b) Custom MCP client** | Zero new deps; full control | Must implement protocol handshake, capability negotiation, transport; must track spec changes manually; reinventing the wheel |

**Transport:** Support both **stdio** (local/dev) and **Streamable HTTP** (remote/production). Skip deprecated SSE.

**Test servers:** Everything (comprehensive reference) + Filesystem (realistic integration).

**Decision:** **(a) Official SDK**, pinned `mcp>=1.25,<2`. Thin `MCPBridgeTool` adapter layer isolates rest of codebase from SDK API changes.

---

### D18: MCP Tool Result Mapping

**Unblocks:** #53

MCP `CallToolResult` has: `content: list[ContentBlock]` (text/image/audio/resource), `structuredContent: dict | None`, `isError: bool`. Our `ToolResult` has: `content: str`, `is_error: bool`.

| Option | Pros | Cons |
|--------|------|------|
| **(a) Extend ToolResult to support multi-modal** | Native support for images/resources | Cascading changes across entire codebase; LLM providers consume tool results as text anyway |
| **(b) Adapter in MCPBridgeTool; keep ToolResult as-is (CHOSEN)** | Zero disruption; text concatenation for LLM path; rich content stored in `ToolExecutionResult.metadata` (not `ToolResult`, which has no metadata field); MCP spec requires TextContent block alongside structured content | Non-text content requires metadata extraction |

**Mapping:**
- Text blocks → concatenate into `content: str`
- Image/audio → `[image: {mimeType}]` placeholder in content; base64 in `metadata["attachments"]`
- `structuredContent` → `metadata["structured_content"]`
- `isError` → `is_error` (direct 1:1)
- `tool_call_id` assigned by our framework, associated back after MCP response

**Decision:** **(b) Adapter in MCPBridgeTool.** Keep `ToolResult` as-is. Handle complexity in the bridge. Future: extend `ToolResult` with optional `attachments` when multi-modal LLM tool results are needed.

---

## Timeout Decisions (D19–D21)

### D19: Timeout — Risk Tier Classification Source

**Unblocks:** #126

| Option | Pros | Cons |
|--------|------|------|
| **(a) Fixed per action type** | Simplest | Rigid; `git_push` might be low-risk for internal team but high-risk for production |
| **(b) SecOps assigns at runtime** | Context-aware | Requires SecOps running; non-deterministic; expensive; blocks timeout on SecOps |
| **(c) Configurable YAML mapping (CHOSEN)** | Follows "Configuration over Code" principle; predictable; matches spec §12.4 examples; hot-reloadable; OPA best practice | Configuration burden (mitigated by sensible defaults) |
| **(d) Default mapping + SecOps override** | Best of both | Premature coupling to SecOps; non-deterministic when SecOps active |

**Decision:** **(c) Configurable YAML mapping.** `RiskTierMapping` config model with `dict[str, ApprovalRiskLevel]`. Sensible defaults matching spec examples. Unknown action types default to HIGH (fail-safe). Leaves door open for future SecOps override.

---

### D20: Timeout — Context Serialization Format

**Unblocks:** #126

| Option | Pros | Cons |
|--------|------|------|
| **(a) Pydantic `model_dump_json()` only** | Natural fit; fast (Rust-based); round-trip fidelity | No queryability; no durability beyond memory |
| **(b) Persistence backend (SQLite) only** | Queryable, durable, transactional | Still needs serialization format |
| **(c) Pydantic JSON via persistence backend (CHOSEN)** | Pydantic handles serialization fidelity; SQLite handles durability + queryability; matches Temporal, LangGraph, SpiffWorkflow patterns | Two serialization boundaries; new repository + migration needed |

**Sub-decision: Verbatim vs summarized conversation?**
- **Verbatim (CHOSEN)** — Every major workflow engine (Temporal, LangGraph) stores full state. Summarization is a context window management concern at resume time, not a persistence concern. No information loss.

**Decision:** **(c) Pydantic JSON via persistence backend.** `ParkedContext` model with metadata columns (execution_id, agent_id, task_id, parked_at) + `context_json` blob. `ParkedContextRepository` protocol. Conversation stored verbatim.

---

### D21: Timeout — Resume Injection

**Unblocks:** #126

| Option | Pros | Cons |
|--------|------|------|
| **(a) System message injection** | Simple to implement | System messages are for instructions, not events; agent may not "notice" mid-conversation; no structured data |
| **(b) Tool result injection (CHOSEN)** | Semantically correct (approval IS the tool's return value); LLM conversation protocol requires tool result after tool call; matches LangGraph HITL pattern; structured data | Requires approval request to be modeled as tool call |
| **(c) Context metadata flag** | Clean separation | LLM doesn't see it — must still inject something into conversation; incomplete on its own |

**Key insight:** If the agent requested approval via a tool call (`request_human_approval`), then the approval decision IS the tool's return value. The LLM expects a tool result before the next assistant turn. Injecting it as a tool result satisfies the protocol and reads naturally in the conversation.

**Decision:** **(b) Tool result injection.** Model approval requests as tool calls. Approval decision returned as `ToolResult`. Fallback for engine-initiated parking: system message (option a) as exception path.

---

## Non-Inferable Prompt Decisions (D22–D23)

### D22: Tools Section Redundancy in System Prompt

**Unblocks:** #188

| Option | Pros | Cons |
|--------|------|------|
| **(a) Remove tools section from system prompt (CHOSEN)** | Eliminates pure duplication (200-400+ tokens per call); both Anthropic and OpenAI inject tool definitions internally via `tools` parameter; the API version is RICHER (includes schemas); saves 20%+ cost per arXiv 2602.11988 | Very minor risk model benefits from seeing tools in different format |
| **(b) Keep as-is** | Belt-and-suspenders | Wastes tokens; increases context rot; contradicts provider best practices |
| **(c) Replace with behavioral guidance** | Non-redundant value ("when searching, prefer grep over reading files") | Requires per-tool-set crafting |

**Evidence:**
- Anthropic docs: "we construct a special system prompt from the tool definitions" via `tools` parameter
- OpenAI staff: "you don't need to repeat the same information in the prompt"
- arXiv 2602.11988: redundant context increases cost 20%+ with minimal or negative success impact
- Chroma "Context Rot" research: performance degrades as input length increases, even below context limit

**Decision:** **(a) Remove.** The tools section provides LESS information than the API injection (no schemas), making it strictly inferior. Later, consider option (c) for behavioral guidance (when to use, not what tools exist).

---

### D23: Memory Filter Heuristic (Non-Inferable)

**Unblocks:** #188

| Option | Pros | Cons | Cost |
|--------|------|------|------|
| **(a) LLM classification at retrieval** | Potentially highest accuracy | 2K-10K extra input tokens per retrieval; adds 0.5-2s latency; classifier itself can hallucinate; recursive problem | Very high |
| **(b) Keyword/pattern heuristic** | Zero token cost | Low accuracy; "auth module at src/auth/ uses JWT" mentions a path but the DECISION is non-inferable; brittle | Free |
| **(c) Tag-based: tagged at write time, filtered at read time (CHOSEN)** | Zero retrieval cost; infrastructure already exists (`MemoryMetadata.tags`, `MemoryQuery.tags`); write-time classification is most accurate (creator has richest context) | Depends on tagging discipline (enforceable at store boundary) | Free |
| **(d) Documentation only** | Zero implementation | Without enforcement, agents WILL store inferable content (arXiv confirms) | Free |

**Decision:** **(c) Tag-based** with **(d) documentation** as complement. Define `non-inferable` tag convention. Enforce at `MemoryBackend.store()` boundary. System prompt instructs agents what qualifies: design rationale, team decisions, "why not X", cross-repo knowledge = non-inferable. Code structure, API signatures, file contents = inferable. Existing `MemoryMetadata.tags` and `MemoryQuery.tags` require zero new models.

---

## Summary of All Decisions

| ID | Decision | Chosen Approach | Protocol | Initial Impl | Unblocks |
|----|----------|----------------|----------|-------------|----------|
| D1 | Action type taxonomy | Enum core + validated registry; two-level `category:action`; static tool metadata | — | StrEnum + `ActionTypeRegistry` | #40, #42, #126 |
| D2 | Quality scoring | Pluggable strategy | `QualityScoringStrategy` | Layered: CI signals + LLM judge + human override (start Layer 1) | #47, #43, #49 |
| D3 | Collaboration scoring | Pluggable strategy | `CollaborationScoringStrategy` | Automated behavioral telemetry | #47, #43, #49 |
| D4 | SecOps: LLM vs rules | Hybrid: rule engine + LLM | — | Rule fast path (~95%) + LLM slow path (~5%) | #40 |
| D5 | SecOps: integration point | Pluggable + configurable | `SecurityInterceptionStrategy` | Before every tool invocation | #40 |
| D6 | Autonomy: scope | Three-level chain | — | Agent → department → company default | #42 |
| D7 | Autonomy: who changes | Pluggable strategy | `AutonomyChangeStrategy` | Human-only promotion | #42 |
| D8 | HR: instantiation | Templates + LLM; persist to DB; hot-plug | — | All three as decided | #45 |
| D9 | HR: task reassignment | Pluggable strategy | `TaskReassignmentStrategy` | Queue-return + priority boost | #45 |
| D10 | HR: memory archival | Pluggable strategy | `MemoryArchivalStrategy` | Full snapshot, read-only | #45 |
| D11 | Perf: rolling window | Pluggable strategy | `MetricsWindowStrategy` | Multiple: 7d, 30d, 90d | #47 |
| D12 | Perf: trend detection | Pluggable strategy | `TrendDetectionStrategy` | Theil-Sen + thresholds | #47 |
| D13 | Promotion: criteria logic | Pluggable strategy | `PromotionCriteriaStrategy` | Threshold gates (N of M) | #49 |
| D14 | Promotion: approval | Pluggable strategy | `PromotionApprovalStrategy` | Senior+ requires human | #49 |
| D15 | Promotion: model mapping | Pluggable strategy | `ModelMappingStrategy` | Default ON, opt-out | #49 |
| D16 | Sandbox: backend | Pluggable (existing) | `SandboxBackend` | Docker only (aiodocker) | #50 |
| D17 | MCP: SDK | Pluggable adapter layer | — | Official `mcp` SDK `>=1.25,<2` | #53 |
| D18 | MCP: result mapping | Pluggable adapter | — | `MCPBridgeTool` adapter | #53 |
| D19 | Timeout: risk tiers | Pluggable strategy | `RiskTierClassifier` | YAML mapping, unknown→HIGH | #126 |
| D20 | Timeout: serialization | Pydantic JSON via persistence | `ParkedContextRepository` | `ParkedContext` model + verbatim | #126 |
| D21 | Timeout: resume | Tool result injection | — | Approval = tool's return value | #126 |
| D22 | Non-inferable: tools | Remove tools from system prompt | — | API injects richer definitions | #188 |
| D23 | Non-inferable: memory | Pluggable strategy | `MemoryFilterStrategy` | Tag-based at write time | #188 |
