---
title: Embedding Model Evaluation
description: LMEB-guided embedding model selection for agent memory retrieval, with taxonomy mapping and fine-tuning pipeline design.
---

# Embedding Model Evaluation

## Why LMEB, Not MTEB

The standard text embedding benchmark ([MTEB](https://huggingface.co/spaces/mteb/leaderboard))
evaluates traditional passage retrieval. SynthOrg's memory system requires **long-horizon memory
retrieval**: fragmented, context-dependent, and temporally distant information across episodic,
procedural, semantic, and social memory types.

The [LMEB benchmark](https://arxiv.org/abs/2603.12572) (Zhao et al., March 2026) evaluates
exactly this: 22 datasets, 193 zero-shot retrieval tasks across four memory types. Its key finding
is that **MTEB performance does not generalise to memory retrieval**:

| Correlation | Pearson | Spearman |
|-------------|---------|----------|
| Overall LMEB vs MTEB | -0.115 | -0.130 |
| Episodic vs MTEB | -0.271 | -0.150 |
| Dialogue vs MTEB | -0.496 | -0.364 |
| Semantic vs MTEB | 0.103 | 0.061 |
| Procedural vs MTEB | 0.291 | 0.429 |

Negative or near-zero correlations mean a model that tops MTEB may perform poorly on the memory
retrieval tasks SynthOrg relies on. Procedural memory shows the strongest (but still weak) transfer,
while dialogue memory shows **anti-correlation**: the worst MTEB models sometimes outperform the
best on dialogue retrieval.

---

## SynthOrg Memory Type Mapping

SynthOrg defines five memory categories (`MemoryCategory` enum). LMEB defines four. The mapping
is direct for three types; two SynthOrg types share a single LMEB category.

| SynthOrg Category | LMEB Category | LMEB Task Examples | Evaluation Priority |
|-------------------|---------------|-------------------|---------------------|
| **EPISODIC** | Episodic | EPBench (54 tasks), KnowMeBench (15 tasks): temporal event recall | **High** |
| **PROCEDURAL** | Procedural | Gorilla, ToolBench, ReMe, MemGovern, DeepPlanning (67 tasks): skill/action retrieval | **High** |
| **SEMANTIC** | Semantic | QASPER, NovelQA, PeerQA, SciFact (15 tasks): factual knowledge recall | Medium |
| **SOCIAL** | Dialogue | LoCoMo, LongMemEval, REALTALK, ConvoMem (42 tasks): multi-turn context | Medium |
| **WORKING** | (not applicable) | Working memory is in-context, not stored/retrieved | N/A |

**Priority rationale**: episodic and procedural memory are the primary retrieval-dependent types in
SynthOrg. Social memory maps to dialogue retrieval (the hardest LMEB category). Semantic memory
is important but shows partial overlap with traditional passage retrieval. Working memory is
in-context and does not use the embedding pipeline.

---

## LMEB Leaderboard Analysis

All scores are NDCG@10 (with instruction prompts unless noted). Source: LMEB paper, Table 3.

### Top Models by Memory Type

| Rank | Model | Params | Episodic | Procedural | Dialogue | Semantic | Overall |
|------|-------|--------|----------|------------|----------|----------|---------|
| 1 | bge-multilingual-gemma2 | 9B | 70.88 | 61.40 | **59.60** | 60.41 | **61.41** |
| 2 | KaLM-Embedding-Gemma3 | 12B | **70.89** | **63.43** | 56.59 | 57.53 | 60.10 |
| 3 | NV-Embed-v2 | 7B | 68.45 | 58.77 | 56.42 | **62.18** | 60.25 |
| 4 | e5-mistral-7b-instruct | 7B | 67.43 | 55.41 | 55.03 | 57.63 | 57.08 |
| 5 | multilingual-e5-large-instruct | 560M | 63.60 | 52.22 | 54.62 | 57.18 | 55.33 |

### Small Models (< 1B parameters)

| Model | Params | Episodic | Procedural | Overall | Notes |
|-------|--------|----------|------------|---------|-------|
| EmbeddingGemma-300M | 307M | - | - | 56.03 (w/o inst.) | Outperforms 9B models without instructions |
| Qwen3-Embedding-0.6B | 596M | - | - | ~53 | Competitive small model |
| Qwen3-Embedding-4B | 4B | - | 59.81 | ~58 | Strong procedural performance |

### Key Findings

1. **Larger does not mean better.** EmbeddingGemma-300M (307M params) scores 56.03 without
   instructions, outperforming bge-multilingual-gemma2 (9B) at 45.10 without instructions.
   Architecture and training data matter more than parameter count.

2. **Instruction sensitivity varies wildly.** Some models gain +3-5% with instructions
   (KaLM-Embedding-Gemma3), others are neutral (NV-Embed-v2), and some are harmed by instructions
   (EmbeddingGemma-300M, bge-m3). Instruction tuning must be validated per deployment.

3. **Dialogue memory is the critical gap.** The highest dialogue score is 59.60 (bge-multilingual-gemma2),
   well below episodic (70.89) and semantic (62.18). This affects SynthOrg's SOCIAL memory quality.

4. **No universal embedding model exists.** No single model excels across all memory types.
   Model selection must be optimised for the deployment's primary memory retrieval pattern.

---

## What the results show

The embedding model is the operator's choice. This section reports what the
published evaluations measured, grouped by the resource class a deployment can
afford, so that choice can be an informed one. Nothing here is a default, and
nothing in the codebase reads it.

### Full-resource deployment (GPU server, 7-12B model)

`bge-multilingual-gemma2` (9B) scores highest overall on LMEB (61.41 NDCG@10)
and leads on dialogue and social recall (59.60), the hardest category, alongside
strong episodic (70.88) and procedural (61.40) results and a consistent +1.96
gain from instruction prompts. `NV-Embed-v2` (7B) leads on semantic recall
instead (62.18) and scores consistently regardless of prompt formatting.

### Mid-resource deployment (consumer GPU, 1-4B model)

`Qwen3-Embedding-4B` (4B) scores 59.81 NDCG@10 on procedural recall with a
reasonable balance across the other memory types, and fits in the 16-24 GB of
VRAM a consumer GPU offers for inference.

### CPU-only or embedded deployment (under 1B model)

`EmbeddingGemma-300M` (307M) scores 56.03 without instructions, competitive with
models an order of magnitude larger, and runs on CPU at a latency asynchronous
retrieval tolerates. Its scores fall when given instruction prompts, so the
figure above is measured without them.

### Embedder Configuration

`EmbedderConfig` binds an explicit `(provider, model)` pair plus the vector width.
Boot resolves it through `resolve_embedder_config`, which reads the YAML override
below as the base, then applies the operator's `memory.embedder_model` setting (a
provider-bound `MODEL_REF`) and the optional `memory.embedder_dims` pin over it,
so a setting wins per field. **It selects nothing**: an unresolved binding leaves
memory OFF and logs why, and the built-in embedder is reachable only by naming it
(`builtin` / `hashing`).

The rankings on this page inform that choice; they do not make it. Nothing in the
codebase reads them, and the tiers below are sizing guidance for an operator.

```yaml
memory:
  embedder:
    provider: "example-provider"
    model: "example-embedding-001"
    dims: 2000                 # optional: pin an MRL truncation of the model's width
```

When `dims` is not pinned, the width is **measured** rather than looked up:
`probe_embedder_dims` embeds a short probe string through the chosen pair and
counts components. A shipped table of catalogued widths cannot be the authority
here, because it goes stale, cannot cover a model it has never heard of, and
being wrong by a single component makes every stored vector incomparable. The
probe doubles as proof the binding works: a model that cannot embed fails at
selection rather than at the first memory write.

A `dims` value the model cannot produce is a hard failure rather than a warning:
vectors of the wrong width corrupt recall silently, which is far worse than not
booting. Setting `embedder_dims` *below* the model's output is the one sanctioned
mismatch: an explicitly pinned narrower width truncates each vector to its leading
components and renormalises it, which is how a Matryoshka-trained model is used at
a smaller width. Truncating a model that was not MRL-trained degrades recall, so
this only ever happens on the operator's explicit instruction, never by inference.

### Dense index width ceilings

pgvector indexes at most 2000 dimensions with a full-precision `vector` and 4000
with a half-precision `halfvec`, so the Postgres backend picks the element type
from the configured width: `vector` at or below 2000, `halfvec` up to 4000. Above
4000 no approximate index can be built at all; the dense column is still created
and dense search still runs, as an exact scan over the corpus, reported at ERROR
with `memory.dense_index.unindexable`. Recall stays semantic either way, but a
width above the ceiling reads every row per query: pin `memory.embedder_dims` at
or below 2000 on an MRL-capable model, or choose a narrower embedder.

That state is reported as DEGRADED on the memory health surface and does **not**
fail readiness. It answers every query correctly and differs only in latency, so
gating traffic on it would take a working system offline; it is also terminal,
since no retry can build an index pgvector refuses for that width, so a readiness
probe would wait out its whole budget for a condition known at boot.

Changing the embedding model after deployment invalidates every stored vector, because
embeddings from different models are not comparable. The dense index is therefore keyed
by width: a new width indexes into a fresh table (SQLite) or column (Postgres) rather
than mixing incompatible vectors into the old one. That keeps recall correct, but it
also means the previous vectors go unreachable, so startup scans for indexes left at
other widths and logs `memory.dense_index.width_changed` at ERROR with the orphaned
count. Without that signal an operator would read the resulting empty recall as a bug
in memory rather than as the consequence of the model swap.

Entries themselves survive a width change: rows, tags, and the lexical index are
width-independent, so recall degrades to lexical-only for the older entries rather than
losing them. Re-embedding restores semantic recall. Plan model selection before the
first production deployment regardless.

---

## Domain Fine-Tuning Pipeline

Whichever model an operator picks, domain-specific fine-tuning can improve retrieval
quality by 10-27% ([NVIDIA blog](https://huggingface.co/blog/nvidia/domain-specific-embedding-finetune),
tested on NVDocs and Jira datasets). The pipeline requires no manual annotation and runs on a
single GPU.

### Pipeline Overview

```mermaid
graph LR
    S1["1. Synthetic Data Generation\nOrg docs, ADRs, procedures\nLLM generates query-doc pairs"]
    S2["2. Hard Negative Mining\nBase model embeds all passages,\nselects top-k confusing negatives"]
    S3["3. Contrastive Fine-Tuning\nInfoNCE loss, tau = 0.02\n3 epochs, lr = 1e-5"]
    S4["4. Evaluation\nNDCG@10 A/B against the incumbent,\npromotion gated on a positive margin"]
    S5["5. Deploy\nSave checkpoint,\nrecord it as active"]
    S1 --> S2 --> S3 --> S4 --> S5
```

### Stage Details

**Stage 1: Synthetic Data Generation**

- Input: organisation documents (policies, ADRs, procedures, coding standards, meeting notes)
- Process: LLM generates realistic retrieval queries for each document chunk
- Output: `(query, positive_document)` pairs
- No GPU required (API-based LLM calls)

**Stage 2: Hard Negative Mining**

- Input: query-document pairs + base embedding model
- Process: embed all passages, compute query-passage similarity, select top-k highest-scoring
  non-positive passages (with margin filter to avoid false negatives)
- Output: `(query, positive, [hard_negative_1, ..., hard_negative_k])` triples
- GPU required (40 GB VRAM for embedding)
- Encoding constraints: per-call `processing_kwargs={"text": {"max_length": N, "truncation": True}}`
  with `_QUERY_MAX_LENGTH = 128` for queries and `_PASSAGE_MAX_LENGTH = 512` for passages.
  Inputs whose word count likely exceeds the token limit emit a
  `memory.fine_tune.encode_truncation_likely` WARNING log.

**Stage 3: Contrastive Fine-Tuning**

- Input: training triples from Stage 2
- Process: biencoder contrastive training with InfoNCE loss, temperature tau=0.02
- Key hyperparameters: 3 epochs, lr=1e-5, batch size 128, 5 passages per query (1 positive + 4 hard negatives)
- GPU required (80 GB VRAM for training, or reduced batch size on smaller GPUs)
- Duration: 1-2 hours for typical org corpus (~500 documents)

**Stage 4: Evaluation**

- Input: held-out validation pairs + fine-tuned checkpoint and base model
- Process: encode queries and passages with both models and compute NDCG@10 and Recall@10
- Re-applies the same query (128) / passage (512) token caps with truncation enabled, so
  eval embeddings are tokenisation-consistent with mining
- Output: `EvalMetrics` snapshot (per-model NDCG@10 / Recall@10) persisted alongside the checkpoint

**Stage 5: Deploy**

- Save fine-tuned model checkpoint to configured path
- Point `EmbedderConfig` at the fine-tuned model, via a provider that serves the
  checkpoint or a local model path
- The fine-tuned model takes effect on the next backend initialisation

### Integration Design

Fine-tuning is an **offline pipeline**, not a runtime operation. The `EmbeddingFineTuneConfig`
(see [Memory Design Spec](../design/memory.md#embedding-model-selection))
stores the configuration. Initialisation behaviour when the embedder is resolved:

1. If `fine_tune.enabled` and `checkpoint_path` is set: the checkpoint path becomes the
   model identifier (the embedding provider must serve the fine-tuned model)
2. If `fine_tune.enabled` is `False` (default): the base model is used, no checkpoint check

A fine-tuned model whose output width differs from the base model is a width change like
any other, with the same consequence for previously stored vectors.

The pipeline is triggered via `POST /admin/memory/fine-tune` (see `MemoryAdminController`).
This follows the project's pattern of disabled-by-default optional features
(cf. `DualModeConfig` in consolidation).

### Improvement Expectations

Based on the NVIDIA evaluation:

| Dataset | Metric | Base | Fine-Tuned | Improvement |
|---------|--------|------|-----------|-------------|
| NVDocs | NDCG@10 | 0.555 | 0.616 | +10.9% |
| NVDocs | Recall@10 | 0.630 | 0.693 | +10.0% |
| Jira (Atlassian) | Recall@60 | 0.751 | 0.951 | +26.7% |

Domain-specific corpora (like organisational documents) tend to see higher gains because the base
model's generic training does not cover domain-specific terminology and relationships.

---

## References

- Zhao et al., ["LMEB: Long-horizon Memory Embedding Benchmark"](https://arxiv.org/abs/2603.12572) (March 2026)
- NVIDIA, ["Domain-Specific Embedding Fine-Tuning"](https://huggingface.co/blog/nvidia/domain-specific-embedding-finetune) (2026)
- [LMEB GitHub Repository](https://github.com/KaLM-Embedding/LMEB): datasets, evaluation code, leaderboard
- [LMEB HuggingFace Dataset](https://huggingface.co/datasets/KaLM-Embedding/LMEB)
