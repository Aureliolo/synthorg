---
title: "Evaluating Agent-Controlled Context Compaction: From Threshold-Based Triggers to Semantic Compression in the Hybrid Loop"
issue: 687
sources:
  - "https://blog.langchain.com/autonomous-context-compression/"
  - "https://huggingface.co/papers/2603.24472"
  - "https://huggingface.co/papers/2603.08462"
  - "https://docs.langchain.com/oss/python/deepagents/context-engineering"
  - "https://arxiv.org/abs/2603.15653"
date: 2026-04-07
related_research_log: 24
---

# Agent-Controlled Context Compaction Evaluation

## Context

LangChain's Autonomous Context Compression proposes exposing compaction as an **agent tool**
rather than a fixed-threshold system trigger. The agent decides when to compact at
semantically meaningful moments such as task boundaries, before large new inputs, after extracting
key results. This aligns with SynthOrg's design principle for auto-downgrade: model changes
apply only at task boundaries, never mid-execution.

SynthOrg's current compaction is threshold-based (80% context fill) with a simple text
concatenation summarizer. This evaluation assesses the current implementation, designs an
agent-controlled alternative, and provides a phased improvement roadmap informed by three
additional research sources on epistemic marker preservation, surprisal-based compression,
and LangChain Deep Agents context engineering thresholds.

---

## Current Compaction Review

### Implementation Inventory

**Configuration** (`src/synthorg/engine/compaction/models.py`):

```python
class CompactionConfig(BaseModel):
    fill_threshold_percent: float = 80.0  # trigger at 80% fill
    min_messages_to_compact: int = 4      # minimum messages before eligible
    preserve_recent_turns: int = 3        # recent turn pairs to keep verbatim
```

**Trigger mechanism** (`src/synthorg/engine/compaction/summarizer.py`):

```python
def _do_compaction(ctx, config, estimator):
    if ctx.context_fill_percent < config.fill_threshold_percent:
        return None  # threshold not met
    # split -> summarize -> return compressed context
```

Trigger checked at turn boundaries in all three loops via shared `invoke_compaction()` in
`src/synthorg/engine/loop_helpers.py` (lines 654-689). Errors are caught, logged as
`CONTEXT_BUDGET_COMPACTION_FAILED`, and never propagated. `MemoryError`/`RecursionError`
are re-raised.

**Conversation splitting** (`_split_conversation`):

- Head: leading SYSTEM messages (preserved verbatim: system prompt, etc.)
- Archivable: middle messages (compressed)
- Recent: last `preserve_recent_turns * 2` messages (preserved verbatim)

**Summary quality** (`_build_summary`, lines 209-250):

```python
for msg in messages:
    if msg.role == MessageRole.ASSISTANT and msg.content:
        cleaned = msg.content.replace("\n", " ").strip()
        snippet = sanitize_message(cleaned, max_length=100)  # 100 chars per snippet
        snippets.append(snippet)
joined = "; ".join(useful)
if len(joined) > _MAX_SUMMARY_CHARS:  # 500 chars total
    joined = joined[:_MAX_SUMMARY_CHARS] + "..."
return f"[Archived {len(messages)} messages. Summary of prior work: {joined}]"
```

This is a mechanical text concatenation with no semantic understanding.

**Context fill estimation** (`src/synthorg/engine/context_budget.py`):

```python
def estimate_context_fill(ctx, estimator):
    system_tokens = estimator.estimate(system_prompt)
    conv_tokens = sum(estimator.estimate(msg.content) for msg in conversation)
    tool_overhead = 50 * len(tool_definitions)  # 50 tokens per tool definition
    return system_tokens + conv_tokens + tool_overhead
```

`DefaultTokenEstimator`: `len(text) // 4` heuristic with 4-token per-message overhead.

### What Works

- **Reliable safety mechanism**: errors are properly isolated; compaction failure never
  kills agent execution. The loop continues even if compaction fails completely.
- **Configurable thresholds**: `CompactionConfig` is part of `AgentEngine` construction,
  giving operators control over trigger point and retention.
- **Compression metadata preserved**: `CompressionMetadata` is serialised with `AgentContext`
  checkpoints, enabling recovery to resume after compaction correctly.
- **Turn-boundary invocation**: Compaction only fires at turn boundaries (never mid-response
  or mid-tool-call), consistent with the "no mid-execution changes" principle.
- **Error isolation architecture**: The `invoke_compaction()` helper wraps the callback in
  try/except, ensuring a broken compaction implementation does not corrupt execution.

### What Does Not Work

**Summary quality is poor.** `_build_summary()` truncates assistant message snippets to 100
characters and concatenates them. This loses:

- Reasoning chains (multi-step thinking truncated at 100 chars)
- Tool use context (what was called, what was found)
- Decision rationale (why a particular approach was chosen)
- Progress state (what was completed, what remains)

The resulting summary is a list of sentence fragments with no structure.

**No semantic awareness.** Compaction triggers at 80% fill regardless of:

- Whether the agent is mid-reasoning (within a complex multi-step analysis)
- Whether the current turn boundary is semantically significant
- The complexity of the task (SIMPLE vs. COMPLEX/EPIC tasks need different strategies)

**No epistemic marker awareness.** Research (arXiv:2603.24472) shows that removing
markers like "wait", "hmm", "actually", "let me reconsider" from reasoning traces degrades
AIME24 accuracy by up to 63%. The current `_build_summary()` truncates these markers
indiscriminately via the 100-char snippet cap and sanitization.

**Fixed threshold regardless of model capacity.** `fill_threshold_percent=80.0` applies
uniformly. A model with a 200k token context window starts compacting at 160k tokens. A
model with a 4k context window compacts at 3.2k tokens. The fixed percentage may be too
aggressive for large-context models and too permissive for small ones.

**No memory offloading.** Archived messages are discarded, converted to a 500-char text
summary. SynthOrg has a `MemoryBackend` (Mem0, Qdrant embedded + SQLite), but compaction
does not write archived content there as episodic memory entries.

---

## LangChain Deep Agents Threshold Comparison

| Parameter | LangChain Deep Agents | SynthOrg Current | Assessment |
|---|---|---|---|
| Offloading threshold | 20,000 tokens | Not implemented | Gap: no file-based offloading |
| Summarization threshold | 85% of model max | 80% of model max | SynthOrg more conservative (earlier trigger): acceptable |
| Recent retention | 10% of context | `preserve_recent_turns * 2` messages (absolute count) | SynthOrg uses absolute count; could be too few for large contexts (3 turns in a 200k context = trivially small) |
| Catch-and-retry | `ContextOverflowError` caught, retry with summary | No explicit catch | Minor gap: SynthOrg relies on threshold trigger rather than error recovery |
| Summarization method | LLM-based | Text concatenation | Significant quality gap |

**Key insight**: The 80% vs. 85% trigger difference is minor. The meaningful gap is
summarization quality (text concatenation vs. LLM-based) and the lack of memory offloading.
SynthOrg's threshold is appropriately conservative.

---

## Epistemic Marker Preservation

### Why It Matters

Self-Distillation & Epistemic Verbalization (arXiv:2603.24472) shows that "thinking tokens"
(hedging, self-correction, uncertainty markers) are functionally important for
out-of-distribution reasoning. In experiments, models that had these markers removed from
their compressed traces degraded by up to 63% on AIME24 benchmarks.

The current `_build_summary()` function provides no protection for these markers. A 500-char
concatenation of message snippets will strip "wait, I think I made an error, let me
reconsider the approach" to "wait, I think I made an error" or less.

The research finding also clarifies when preservation matters:

- **Narrow/repetitive tasks**: Concise reasoning is fine, marker preservation is not critical
- **Diverse/novel/complex tasks**: Full uncertainty-aware style must be preserved

This maps directly to SynthOrg's `task.estimated_complexity` field.

### Proposed Implementation

**Step 1: Epistemic marker pattern set**

```python
EPISTEMIC_MARKER_PATTERNS: frozenset[re.Pattern] = frozenset({
    re.compile(r'\b(wait|hmm|actually|hm|ah)\b', re.IGNORECASE),
    re.compile(r'\b(let me reconsider|on second thought|I was wrong)\b', re.IGNORECASE),
    re.compile(r'\b(perhaps|alternatively|I\'m not sure|uncertain)\b', re.IGNORECASE),
    re.compile(r'\b(check|verify|double-check|let me verify)\b', re.IGNORECASE),
    re.compile(r'\b(but wait|actually no|hold on)\b', re.IGNORECASE),
})
```

**Step 2: Marker density scoring**

```python
def _count_epistemic_markers(text: str) -> int:
    return sum(
        1 for pattern in EPISTEMIC_MARKER_PATTERNS
        if pattern.search(text)
    )
```

**Step 3: Preservation in `_split_conversation`**

When splitting into (head, archivable, recent), promote archivable messages with marker
density above a threshold from archivable to recent (preserved verbatim). The threshold
depends on `task.estimated_complexity`:

- SIMPLE/MEDIUM: promote if `_count_epistemic_markers(msg.content) >= 3`
- COMPLEX/EPIC: promote if `_count_epistemic_markers(msg.content) >= 1`

**Step 4: Annotation in summary**

When epistemic markers are detected but a message must still be compressed (not promoted
due to size constraints), inject the literal marker phrases into the summary annotation:
`[Archived N messages. Uncertainty points preserved: "wait", "actually", ...]`

This gives the agent receiving the compressed context the signal that there were reasoning
inflection points in the archived section.

---

## Surprisal-Based Semantic Token Cost

### Research Finding

Reasoning as Compression / CIB (arXiv:2603.08462, ICML 2025) proposes using surprisal
under a frozen base model to assign per-token compression cost:

- High-surprisal tokens (novel, unexpected content) = high cost to remove = preserve
- Low-surprisal tokens (predictable filler) = low cost to remove = compress aggressively
- Result: 41% token reduction with <1.5% accuracy drop
- Beta parameter provides smooth accuracy-efficiency tradeoff (maps to quota degradation)

### Feasibility Assessment

**Computational cost**: Surprisal scoring requires a forward pass through a frozen base
model for every token being evaluated for compression. For a 100k-token context, this is
a non-trivial inference call, potentially more expensive than the compaction it enables.

**Infrastructure cost**: SynthOrg would need to maintain a frozen reference model (separate
from the active completion provider) for surprisal scoring. This conflicts with the goal
of being provider-agnostic (LiteLLM-based).

**Proxy options** (lighter approximation):

1. **TF-IDF importance**: Score tokens by inverse document frequency across the
   conversation. Repeated, common tokens score low; rare, specific tokens score high.
   O(V) computation (vocabulary size), no model inference required.
2. **Entropy-based**: Measure information density by character/token entropy of windows.
   Simple statistical measure, no model inference.

**Recommendation**: Full surprisal scoring is not justified for MVP or Phase 2. The
computational and infrastructure cost outweighs the benefit at SynthOrg's current scale.
The finding is valuable as a design principle (compress low-information content, preserve
high-information content) but the implementation should use a lightweight proxy.

**TF-IDF as Phase 2 addition**: After LLM-based summarization is in place, adding TF-IDF
importance scoring to the archival decision (which messages to compress vs. preserve) is a
low-cost improvement that captures the spirit of surprisal-based compression without model
inference overhead.

**Beta-to-DegradationAction mapping**: the conceptual insight (the accuracy-efficiency
tradeoff maps to quota degradation) is actionable without full surprisal scoring. Under
budget pressure (e.g., `QuotaCheckResult.action == DegradationAction.FALLBACK`), use a
tighter compaction threshold (compact at 70% instead of 80%) and less retention (2 turns
instead of 3). This is the beta parameter expressed via existing degradation infrastructure.

---

## Agent-Controlled Compaction Tool Design

### Design Rationale

The fundamental insight from LangChain's Autonomous Context Compression is that an agent
executing a task knows when it is at a semantically good moment to compact:

- After completing a sub-goal ("I've gathered all the data I need, now I'll analyse it")
- Before ingesting a large new tool result
- At a plan step boundary (already natural in HybridLoop)

The threshold-based trigger cannot know these moments. An agent with context fill at 60%
might be at a perfect compaction moment; an agent at 79% might be mid-reasoning.

### Tool Definition

```python
class CompressContextTool(BaseTool):
    """Allow agent to voluntarily compact its context at semantically meaningful moments."""

    name: ClassVar[str] = "compress_context"
    description: ClassVar[str] = (
        "Compact the conversation history to free context space. "
        "Use at task boundaries, before large tool results, or after extracting key findings. "
        "Recent turns are always preserved. Current context fill: {fill_pct}%."
    )

    class Parameters(BaseModel):
        strategy: Literal["summarize", "archive"] = "summarize"
        preserve_markers: bool = True
        reason: NotBlankStr  # agent must state why it's compacting now

    async def execute(self, params: Parameters, context: ToolContext) -> ToolExecutionResult:
        # Returns a compaction directive; actual compaction applied by loop
        return ToolExecutionResult(
            content=f"Compaction requested: {params.reason}",
            metadata={"compaction_directive": True, "strategy": params.strategy,
                      "preserve_markers": params.preserve_markers},
        )
```

### Key Design Challenge: Context Mutation from Tools

The current tool contract (`execute_tool_calls` in `loop_helpers.py`) produces
`ToolExecutionResult` objects and appends them as TOOL messages to `AgentContext`. Tools
cannot mutate the conversation; they can only add a message. `AgentContext` is a frozen
Pydantic model; `with_compression()` is the only mutation method, and it must be called
from the loop, not from a tool.

**Solution (Option A: compaction directive)**:

The `compress_context` tool returns a `ToolExecutionResult` with `metadata["compaction_directive"] = True`.
After `execute_tool_calls()` processes all tool calls in a batch, the loop checks for any
compaction directive in the results and applies compaction via `invoke_compaction()`:

```python
# In loop_helpers.py execute_tool_calls() or in the loop's per-turn handler:
if any(r.metadata.get("compaction_directive") for r in tool_results):
    compacted = await invoke_compaction(ctx, compaction_callback, turn_number)
    if compacted is not None:
        ctx = compacted
```

This preserves the immutable context pattern: the tool signals intent, the loop applies the
change. The agent sees the compaction result in the next turn's context fill indicator.

This is consistent with the existing architecture where the loop, not tools, manages
`AgentContext` state transitions.

### Integration Points

1. `AgentEngine._make_tool_invoker()`: Register `CompressContextTool` alongside memory
   tools when `CompactionConfig.agent_controlled = True`.
2. `loop_helpers.execute_tool_calls()`: Add compaction directive detection post-tool-batch.
3. System prompt guidance: Add to context budget indicator: "Consider using `compress_context`
   before large tool results or at task boundaries."
4. Only expose in HybridLoop initially (where step boundaries are natural compaction moments).
   Extend to ReactLoop and PlanExecuteLoop after validation.

### Dual-Threshold Safety Net

When `agent_controlled=True`, the agent is expected to compact voluntarily. The threshold-
based compaction remains as a safety net but at a higher threshold:

| Mode | Agent Action | System Action |
|---|---|---|
| `agent_controlled=False` (current) | No tool available | Auto-compact at `fill_threshold_percent` (80%) |
| `agent_controlled=True` (proposed) | `compress_context` tool available | Auto-compact at `safety_threshold_percent` (95%) |

New `CompactionConfig` fields:

```python
agent_controlled: bool = False              # opt-in
safety_threshold_percent: float = 95.0     # system fallback when agent_controlled=True
```

The 95% safety net ensures context overflow cannot happen even if an agent never invokes
the tool. Log event `CONTEXT_BUDGET_COMPACTION_SAFETY_NET` when system falls back, for
monitoring agent compaction behaviour.

---

## Phased Implementation Roadmap

### Phase 1: Minimal Improvements (No Architecture Change)

Target files: `src/synthorg/engine/compaction/summarizer.py`, `src/synthorg/engine/compaction/models.py`

1. **Epistemic marker detection in `_build_summary()`**: Add `EPISTEMIC_MARKER_PATTERNS`
   and `_count_epistemic_markers()`. Promote high-marker messages from archivable to
   preserved. Task-complexity-adaptive threshold.

2. **Safety net threshold field**: Add `safety_threshold_percent: float = 95.0` to
   `CompactionConfig`. Use this when `agent_controlled=True` (which defaults to False, so
   no behaviour change in Phase 1, just preparatory).

3. **Relative retention option**: Add `preserve_recent_percent: float | None = None` to
   `CompactionConfig`. When set, retain the larger of `preserve_recent_turns` and
   `floor(context_capacity * preserve_recent_percent / 100 / avg_turn_tokens)`. Addresses
   the LangChain Deep Agents 10% retention recommendation.

These changes improve compaction quality with no architectural change. Epistemic marker
preservation is the highest-value item here.

### Phase 2: Agent-Controlled Tool + LLM Summarization

Target files: New `src/synthorg/engine/compaction/tool.py`, `src/synthorg/engine/loop_helpers.py`,
`src/synthorg/engine/agent_engine.py`

1. **`compress_context` tool**: Implement `CompressContextTool` following the `BaseTool`
   pattern. Wire into `_make_tool_invoker()` when `CompactionConfig.agent_controlled=True`.

2. **Compaction directive handling**: Add directive detection in `execute_tool_calls()`.
   Apply compaction when directive is found in tool results.

3. **LLM-based summarization in `_build_summary()`**: Replace text concatenation with an
   LLM summary call. The summary prompt should ask for: current task progress, key findings
   so far, unresolved questions, next steps. Cost this as `LLMCallCategory.SYSTEM`.

4. **Memory offloading**: Write archived messages to `MemoryBackend` as `MemoryType.EPISODIC`
   entries before discarding them. The agent can retrieve them via episodic memory retrieval
   if needed later.

### Phase 3: Advanced Compression (Data-Driven)

1. **TF-IDF importance scoring**: Score messages by information density before archival
   decision. High-TF-IDF messages are promoted to preserved; low-TF-IDF messages are
   candidates for more aggressive compression.

2. **Adaptive threshold under budget pressure**: When `QuotaCheckResult.action ==
   DegradationAction.FALLBACK`, reduce `fill_threshold_percent` by 10% and `preserve_recent_turns`
   by 1. This implements the beta-parameter concept from arXiv:2603.08462 using existing
   degradation infrastructure.

3. **Per-agent personality-aware marker preservation**: Agents with `verbosity=VERBOSE` or
   `decision_making=DELIBERATIVE` in `PersonalityConfig` use COMPLEX-level marker preservation
   thresholds regardless of task complexity.

---

## Summary of Recommendations

| Priority | Change | Scope | Phase |
|---|---|---|---|
| 1 | Epistemic marker preservation in `_build_summary()` | Small | Phase 1 |
| 2 | `compress_context` tool for HybridLoop | Medium | Phase 2 |
| 3 | LLM-based summarization | Medium | Phase 2 |
| 4 | Dual-threshold safety net (`safety_threshold_percent`) | Small | Phase 1/2 |
| 5 | Memory offloading for archived turns | Medium | Phase 2 |
| 6 | Relative retention option | Small | Phase 1 |
| 7 | TF-IDF importance scoring | Small-Medium | Phase 3 |
| 8 | Adaptive threshold under degradation | Small | Phase 3 |

**Do not implement** full surprisal-based token cost scoring (arXiv:2603.08462) without
benchmarking the inference cost against the compression benefit on SynthOrg's task
distribution. The conceptual insight is valuable; the full implementation is premature.
