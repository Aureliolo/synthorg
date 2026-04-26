---
title: "AI Agent Traps: Execution Safety Threat Model"
description: >
  Maps 6 adversarial-content attack classes to SynthOrg security
  modules, identifies gaps, and cross-references the S1 15-risk
  register for multi-agent coordination risks.
issue: "#1268"
sources:
  - "AI Agent Traps (SSRN:6372438): taxonomy sourced from #1256 issue body"
  - "S1 Multi-Agent Decision Framework (docs/research/s1-multi-agent-decision.md)"
date: 2026-04-14
---

# Execution Safety Threat Model

## Source Caveat

The AI Agent Traps paper (SSRN:6372438) was inaccessible during research
(SSRN returned HTTP 403). The 6-class taxonomy used in this threat model
is sourced from the [#1256 issue body](https://github.com/Aureliolo/synthorg/issues/1256).
When the paper becomes accessible, this threat model must be updated to
cite the paper directly and verify alignment with the original taxonomy.

## Coverage Summary

| # | Class | Coverage | Gap | New Mitigation |
|---|-------|----------|-----|----------------|
| 1 | Content Injection | Partial | Render-time parse gap | `HTMLParseGuard` |
| 2 | Semantic Manipulation | Partial | No per-turn drift detection | `SemanticDriftDetector` |
| 3 | Cognitive State / Memory Poisoning | Strong | RAG / vector-store integrity verification | Threat model only |
| 4 | Behavioural Control / Tool Hijacking | Strong | No registry integrity check | `ToolRegistryIntegrityCheck` |
| 5 | Systemic / Cascading Failure | Covered | None | S1 cross-reference |
| 6 | HITL Cognitive Bias Exploitation | Partial | No bias-specific UI | Threat model only |

---

## Class 1: Content Injection (HTML/Parse/Render Gap)

**Threat**: Attackers inject hidden content into HTML pages fetched by
web-browsing tools. The injected content is invisible to human review
but parsed by the LLM, potentially overriding instructions or
exfiltrating data through tool calls.

**Attack vectors**:

- CSS `display:none` / `visibility:hidden` elements containing prompt injection
- HTML comments with instruction overrides
- `<script>` tags that execute in parsing contexts
- Whitespace manipulation creating semantic gaps between displayed and parsed content

**Existing coverage**:

- `sanitize_message` in the execution pipeline strips basic unsafe content
- Output scanning (`security/rules/`) detects credentials in tool output

**Gap**: No systematic parse-gap detection between raw HTML and rendered text
content consumed by the LLM.

**New mitigation**: `HTMLParseGuard` (`src/synthorg/tools/html_parse_guard.py`)
parses HTML output with `lxml`, strips script/style/noscript/hidden elements,
detects render gaps where visible text differs substantially from raw HTML,
and logs `TOOL_HTML_PARSE_GAP_DETECTED` events. Integrated into
`ToolInvoker._apply_html_guard()` post-execution.

**Residual risk**: Sophisticated CSS-based injection that doesn't use
`display:none` patterns (e.g. negative margins, font-size:0) may evade
detection. The gap threshold (default 5%) can be tuned.

---

## Class 2: Semantic Manipulation

**Threat**: Subtle steering of agent reasoning through adversarial content
that shifts the agent's output away from the task's intended objectives
without triggering explicit content filters.

**Attack vectors**:

- Gradual context drift across multi-turn conversations
- Adversarial prompt framing in tool outputs
- Authority impersonation in retrieved documents

**Existing coverage**:

- R2 verification stages validate execution quality against rubrics
- `AuthorityDeferenceGuard` (S1 risk 2.2) strips authority cues from
  transcripts and injects mandatory-justification prompts

**Gap**: No per-turn detection of semantic drift between model output and
task acceptance criteria.

**New mitigation**: `SemanticDriftDetector` middleware
(`src/synthorg/engine/middleware/semantic_drift.py`) compares model output
against task acceptance_criteria using a token-overlap similarity heuristic
(shipped default). Drift below threshold (default 0.35) logs
`MIDDLEWARE_SEMANTIC_DRIFT_DETECTED` at WARN and annotates
`TurnRecord.semantic_drift_score`. Fail-soft: never blocks execution.
Opt-in via `AgentMiddlewareConfig.semantic_drift.enabled`.

**Residual risk**: The shipped token-overlap heuristic has limited semantic
understanding. Production deployments should override `_compute_similarity`
with embedding-based cosine similarity (configure via
`SemanticDriftConfig.embedding_model`).

---

## Class 3: Cognitive State / Memory Poisoning

**Threat**: Corrupting retrievable context (memory stores, knowledge
bases, procedural memories) with adversarial data that biases future
reasoning or plants hidden instruction backdoors.

**Attack vectors**:

- Injecting false facts into the shared knowledge store
- Poisoning procedural memory generation with misleading patterns
- Manipulating embedding similarity to surface adversarial content

**Existing coverage**:

- Procedural memory generation guards in `memory/procedural/`
- MVCC `SharedKnowledgeStore` with versioned writes preventing silent
  overwrites
- `KnowledgeArchitect` audit (issue #1266) validates knowledge quality

**Gap**: No automated integrity verification of RAG vector stores.
Poisoning detection relies on manual audit and quality verification.

**New mitigation**: Threat model documentation only. The existing
defense-in-depth (MVCC writes, procedural guards, quality verification)
provides strong coverage. Automated RAG-store integrity verification
is a future enhancement.

**Residual risk**: Sophisticated poisoning that produces high-quality
but subtly misleading content may pass quality checks.

---

## Class 4: Behavioural Control / Tool Hijacking

**Threat**: Agents misuse permitted tools for unintended purposes, or
adversarial inputs cause tools to be invoked with harmful parameters.

**Attack vectors**:

- Prompt injection causing tool calls with attacker-specified arguments
- Tool definition tampering at runtime
- Privilege escalation through tool composition

**Existing coverage**:

- Tool permissions (`ToolPermissionChecker`) with per-category gating
- Sandbox isolation (`tools/sandbox/`) with Docker/subprocess backends
- `wrap_tool_call` middleware slot for pre-execution security checks
- `PolicyEngine` (Cedar) for runtime pre-execution policy evaluation

**Gap**: No verification that tool definitions haven't been modified
since the last known-good state.

**New mitigation**: `ToolIntegrityChecker`
(`src/synthorg/tools/integrity_check.py`) computes SHA-256 hashes of
each `ToolDefinition` at boot and compares against recorded hashes.
Mismatches trigger `TOOL_REGISTRY_INTEGRITY_VIOLATION` at ERROR.
Configurable: `fail_on_violation=True` raises `RuntimeError` to block
startup.

**Residual risk**: Boot-time verification doesn't detect runtime
tool definition mutation (frozen Pydantic models prevent this at
the language level, but MCP-bridged tools could theoretically change).

---

## Class 5: Systemic / Cascading Failure

**Threat**: Single faults (hallucinations, poisoned tool outputs,
coordination failures) propagating autonomously across multiple
agents, compounding into widespread service failures.

**Existing coverage**:

- S1 15-risk register with mitigations for all systemic risks
  (see [s1-multi-agent-decision.md](s1-multi-agent-decision.md) section 3)
- Circuit breakers via `BudgetEnforcer` with per-task and daily limits
- `StagnationDetector` with configurable thresholds
- `CoordinationReplanHook` with `max_stall_count` / `max_reset_count`
  hard caps preventing infinite replan loops
- Team-size bounds (3-4 per coordination group, 8 hard cap per meeting)
- `AssumptionViolationSignal` propagated as escalation events

**Gap**: None identified.

**Residual risk**: Novel failure modes not covered by the 15-risk
register. Continuous monitoring and periodic risk register updates
are recommended.

---

## Class 6: HITL Cognitive Bias Exploitation

**Threat**: Exploiting human over-reliance on agent recommendations
and authority bias to trick operators into approving harmful actions
or disclosing sensitive information.

**Attack vectors**:

- Overwhelming operators with frequent low-risk approvals to induce
  approval fatigue before a high-risk request
- Framing high-risk actions as routine or previously approved
- Exploiting time pressure in approval timeout policies

**Existing coverage**:

- `EvidencePackage` (R4 #1263) provides structured HITL approval
  artifacts with `RecommendedAction` options and `narrative` context
- `AuditChainSink` creates tamper-evident trails of all approval
  decisions
- `ApprovalGate` with configurable timeout policies (wait-forever,
  auto-deny, tiered, escalation chain)

**Gap**: No cognitive-bias-specific warnings in the dashboard UI.
The EvidencePackage structure supports bias mitigation (narrative
context, multiple recommended actions) but the UI doesn't currently
surface bias-specific cues (e.g. "This is the 5th approval in 10
minutes; consider slowing down").

**New mitigation**: Threat model documentation only. UI-level
cognitive bias warnings are a future dashboard enhancement.

**Residual risk**: Sophisticated social engineering through agent
outputs that are technically accurate but strategically misleading.

---

## S1 Cross-Reference

The S1 multi-agent decision framework
([s1-multi-agent-decision.md](s1-multi-agent-decision.md) section 3)
covers 15 emergent risks from multi-agent cooperation. Key overlaps
with this threat model:

| S1 Risk | This Threat Class | Overlap |
|---------|-------------------|---------|
| 2.2 Authority deference | Class 2, Class 6 | AuthorityDeferenceGuard addresses both adversarial manipulation and HITL bias |
| 3.2 Over-adherence | Class 2 | AssumptionViolationSignal detects rigid adherence to potentially compromised instructions |
| 4.3 Semantic drift in handoffs | Class 2 | DelegationChainHashMiddleware + SemanticDriftDetector |
| 1.4 Strategic info withholding | Class 3 | Memory poisoning via selective information omission |

This threat model focuses on adversarial-content risks from external
sources; S1 focuses on emergent risks from multi-agent coordination.
Together they cover the full execution safety surface.
