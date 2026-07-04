---
title: Toolsmith (Self-Extending Toolkit)
description: How the organisation extends its own MCP tool surface at runtime when it hits a recurring capability gap, governed end to end.
---

# Toolsmith (Self-Extending Toolkit)

A fixed toolset caps what the studio can build. The **toolsmith** (`src/synthorg/meta/toolsmith/`) lets the organisation extend its own MCP tool surface at runtime when it hits a recurring capability gap, governed end to end. It is part of the broader [tool system](tools.md).

**Detection.** Every unfulfilled capability request is recorded into a ring-buffered `CapabilityGapStore` (the `ToolsmithService` is the sink). The sink is installed at boot (`install_capability_gap_sink`), so both the unknown-tool dispatch path and every `capability_gap` MCP envelope feed the store; the record is best-effort and a write failure logs without a traceback (SEC-1) rather than blocking the request. Each observation carries a `GapKind` that separates the two signals in recurrence analysis:

- **`MISSING_TOOL`** -- an agent requested a tool that does not exist on the surface (the unknown-tool path). This is the genuine novel-capability demand the toolsmith authors for.
- **`SERVICE_ABSENT`** -- a wired MCP handler could not run because its backing SynthOrg service is not implemented in this deployment (a `capability_gap` envelope). This is a framework gap to implement in SynthOrg, NOT a tool to author, so a recurring one raises an operator ops signal (a `SYSTEM`/`WARNING` `Notification`) and is never authored.

When a `(signature, kind)` recurs at least `gap_recurrence_threshold` times within `gap_window_hours`, it qualifies as a recurring gap. Detection is autonomous: a periodic `ToolsmithCycleScheduler` drives `ToolsmithService.run_cycle()` on a cadence (`toolsmith.cycle_interval_seconds`, default one hour) so a recurring `MISSING_TOOL` gap becomes a proposal without an operator trigger. A `meta.toolsmith_cycle_paused` kill-switch (re-read each tick, fail-safe to enabled) lets an operator halt self-extension at runtime without a restart. The cycle only proposes; the governance and validation steps below still gate every authored tool.

**Authoring.** `LLMToolBlueprintGenerator` authors a `ToolBlueprint` from the gap: a declarative spec (name, capability, JSON Schema, action type) plus a self-contained Python `script_body`. The tool name is derived from the capability so it always satisfies the `synthorg_{domain}_{action}` contract; the sandbox backend and network policy come from config, never the model, so an authored tool cannot widen its own isolation. Capabilities that need service-layer access (configured via `service_access_capabilities`) cannot be a sandbox script and route to the `CODE_MODIFICATION` overflow handler instead.

**Governance.** Tool creation runs at the `TOOL_CREATION` proposal altitude behind the same guard chain as self-improvement (scope, rollback plan, rate limit, mandatory approval). The `tool:create` action type is HIGH risk and human-gated under `supervised` and `semi` autonomy. Nothing is trusted without human approval.

**Approve-to-live.** When a `MISSING_TOOL` gap is authored, the service persists the `PENDING` blueprint durably before the approval gate registers its item, and the gate stamps the blueprint id + concrete tool detail (name, capability, description) into the `proposal:tool_creation` approval metadata, so the Approvals screen shows a reviewable "Proposed tool" summary. When an operator approves it, the `ToolApprovalConsumer` (run each toolsmith cycle) rehydrates the blueprint by id, atomically claims the one-shot grant (`consume_if_approved`), and applies it through the service, which re-checks the live `tool_creation` master gate. Human approval is the sole apply trigger: a re-poll never double-applies (the claim is atomic), and a toggle-off of the master gate blocks even an approved tool from registering. The master switch stays OFF by default and the capability allowlist stays curated; approve-to-live adds the missing apply step without loosening either.

**Validation.** On approval, `ToolCreationApplier` runs the `BenchmarkToolValidationGate`: a focused per-tool acceptance brief (the authored script actually runs in its resolved sandbox and must return structured output) followed by a golden-company scorecard delta (registering the candidate must not regress the benchmark). A failing gate registers nothing; the blueprint keeps its validation record for audit but never goes `ACTIVE`.

**Live registration.** A validated blueprint is persisted (PENDING -> VALIDATED -> ACTIVE; RETIRED on rollback) and registered into the mutable `DynamicToolRegistry`. The static `DomainToolRegistry` stays frozen; a `LayeredToolRegistry` reads the static surface first then the dynamic layer, so `MCPToolInvoker` dispatches authored tools (validating arguments against a Pydantic args model materialised from the blueprint's JSON Schema) without unfreezing anything. A later task invokes the new tool exactly like a built-in.

The toolsmith is disabled by default (`meta.self_improvement` -> `tool_creation_enabled`); it wires at boot only when enabled, a provider is registered, and persistence is connected.
