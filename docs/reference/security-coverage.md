---
title: Security Coverage and Operational Bounds
description: OWASP Agentic Top 10 coverage matrix and the session-revalidation revocation window an operator or auditor consults.
---

# Security Coverage and Operational Bounds

This reference collects the two security facts an operator or auditor consults most often: the OWASP Agentic Top 10 coverage matrix and the bound on how quickly a revoked session stops working. The full security architecture lives in the [Security design spec](../design/security.md).

## OWASP Agentic Top 10 (ASI) Coverage Matrix

This matrix maps SynthOrg security mechanisms to the [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/). Coverage is independently derived from codebase analysis and may not be fully aligned with OWASP ASI specifications. Operators should cross-reference with official OWASP documentation.

| ASI | Risk | Coverage | Primary Modules |
|-----|------|----------|----------------|
| ASI01 | Agent Goal Hijack | Partial | `security/rules/` (credential/path detectors), `engine/classification/` (semantic detectors), `HTMLParseGuard` (tool output sanitization), `SemanticDriftDetector` (middleware) |
| ASI02 | Tool Misuse and Exploitation | Covered | `PolicyEngine` (Cedar pre-exec gate), `security/rules/` (preventive rule engine), `tools/sandbox/` (Docker/subprocess isolation), `ApprovalGate` |
| ASI03 | Identity and Privilege Abuse | Covered | 4 autonomy levels, `AuthorityDeferenceGuard`, `ApprovalGate`, delegation budget, `ToolPermissionChecker` (granting ELEVATED tool access always requires human approval) |
| ASI04 | Agentic Supply Chain Vulnerabilities | Partial | `ToolRegistryIntegrityCheck` (boot-time hash verification), pip-audit/npm-audit/Trivy in CI, cosign signatures, SLSA provenance. **Gap**: no runtime plugin integrity verification beyond boot-time hash. |
| ASI05 | Unexpected Code Execution (RCE) | Covered | `tools/sandbox/` (Docker with ephemeral containers, subprocess with env filtering), gVisor runtime for high-risk categories (`code_execution`, `terminal`), `SandboxCredentialManager`, workspace boundary enforcement |
| ASI06 | Memory and Context Poisoning | Partial | Procedural memory generation guards, MVCC `SharedKnowledgeStore`, `SemanticDriftDetector`. **Gap**: no automated RAG-store integrity verification. |
| ASI07 | Insecure Inter-Agent Communication | Partial | `DelegationChainHashMiddleware` (content hash on delegation chain), `AuthorityDeferenceGuard` (strips authority cues from transcripts). **Gap**: no message-level encryption. Not required for the default in-process deployment; reassess before running agents cross-process or cross-host. |
| ASI08 | Cascading Failures | Covered | S1 15-risk register mitigations, circuit breakers (`BudgetEnforcer`), `StagnationDetector`, the initiative rollup's `stall_reason`, dependency-gated waves parking work whose inputs died, team-size bounds (3-4 per group, 8 per meeting) |
| ASI09 | Human-Agent Trust Exploitation | Partial | `EvidencePackage` (structured HITL artifacts with `RecommendedAction` options), `AuditChainSink` (tamper-evident decision trail), `ApprovalGate` with configurable timeout policies. **Gap**: no cognitive-bias-specific UI warnings. |
| ASI10 | Rogue Agents | Covered | 4 autonomy levels (full/semi/supervised/locked), `PolicyEngine` (pre-exec gate), tool permissions (`ToolPermissionChecker`), sandbox isolation, `ToolRegistryIntegrityCheck`, budget limits, `AuthorityBreachDetector` |

**Summary**: 5 covered, 5 partial, 0 uncovered. Partial gaps are documented above with specific module references.

## Session Revalidation and the Revocation Window

Long-lived authenticated streams (WebSocket and SSE) do not trust the access token for their full lifetime. Both re-load the user record on a single shared cadence, `AUTH_REVALIDATE_INTERVAL_SECONDS` (10 minutes), and tear the stream down when the user is deleted, the role is demoted below read access, or the session JTI has been revoked (an admin `DELETE /sessions/{jti}`).

Operationally this means **revocation takes effect within at most one revalidation interval (10 minutes), not instantly**. An access token or open stream remains usable until the next revalidation tick after the revoking action. The refresh-rotation endpoint (`POST /auth/refresh`) rejects immediately on a revoked session, but an already-issued, not-yet-expired access token on an open WS/SSE stream is only kicked at the next tick. Size the JWT lifetime and any incident-response runbook around this 10-minute bound.

WS and SSE share one cadence constant and one sliding-window failure model. Transient persistence-backend errors during a revalidation tick are admitted into a per-connection sliding window (`api.auth_revalidate_window_seconds`, default 60s; `api.auth_revalidate_max_failures`, default 5). Failures age out of the window instead of resetting on success, so a flaky backend that interleaves one good response between failure clusters cannot hold a stale-auth stream open indefinitely; once the window saturates the stream closes (WS: close code 4011; SSE: a final `revoked` frame with `reason=backend_unavailable`) and the client reconnects against a healthy replica.

Both failure-tolerance settings are sampled per new connection-open: a change applies to subsequently-opened streams with no restart, but does not retune already-open streams. The unified sliding-window model is shared verbatim between WebSocket and SSE.

429 rate-limit `Retry-After` is per-policy (per-operation budgets, account-lockout duration) and is intentionally **not** coupled to the revalidation cadence; the unified cadence governs WS and SSE auth revalidation only.
