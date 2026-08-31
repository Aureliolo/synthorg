import type { ProviderModelConfig } from "@/api/types/providers";

function parsePositiveInt(raw: string): number | null {
  const t = raw.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && Number.isInteger(n) && n > 0 ? n : null;
}

function parseNonNegFloat(raw: string): number | null {
  const t = raw.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

type FieldParse = { ok: true; value: number | null } | { ok: false };

/** Parse an optional numeric field: empty → null (use default), invalid → not-ok. */
function parseOptionalField(
  raw: string,
  parse: (r: string) => number | null,
): FieldParse {
  if (raw.trim() === "") return { ok: true, value: null };
  const value = parse(raw);
  return value === null ? { ok: false } : { ok: true, value };
}

export interface ManualModelInputs {
  modelId: string;
  alias: string;
  costInput: string;
  costOutput: string;
  costImage: string;
  maxContext: string;
  latencyMs: string;
}

interface ParsedModelValues {
  idTrimmed: string;
  alias: string;
  ctx: number | null;
  inCost: number | null;
  outCost: number | null;
  imageCost: number | null;
  latency: number | null;
}

function buildManualModel(v: ParsedModelValues): ProviderModelConfig {
  return {
    id: v.idTrimmed,
    alias: v.alias.trim() || null,
    // No override yet: an operator can declare one after the model exists.
    capability_overrides: null,
    cost_per_1k_input: v.inCost ?? 0,
    cost_per_1k_output: v.outCost ?? 0,
    cost_per_image: v.imageCost,
    max_context: v.ctx ?? 200_000,
    estimated_latency_ms: v.latency ?? null,
    local_params: null,
    // Manually-added models are unenriched; backend enriches on next sync.
    // Two capabilities gate design-tool usability before that sync, so apply
    // a last-resort heuristic for each: embedding by id-substring (so an
    // embedder-by-id is not treated as a chat candidate), and image
    // generation from a configured per-image cost (the operator's deliberate
    // signal that this is an image-output model, otherwise unselectable as
    // design.image_model until a sync re-enriches it).
    metadata: {
      supports_tools: false,
      supports_vision: false,
      supports_reasoning: false,
      supports_embeddings: /embed/i.test(v.idTrimmed),
      supports_image_generation: v.imageCost !== null,
      supports_prompt_caching: false,
      max_output_tokens: null,
      parameter_count: null,
      cost_tier: null,
      family: null,
      generation: null,
      release_date: null,
      tool_calls_verified: null,
      metadata_source: "unknown",
    },
    stale: null,
  };
}

export type ManualModelValidation =
  | { error: string }
  | { model: ProviderModelConfig };

export function validateManualModel(
  fields: ManualModelInputs,
): ManualModelValidation {
  const idTrimmed = fields.modelId.trim();
  if (idTrimmed === "") return { error: "Model id is required." };
  const ctx = parseOptionalField(fields.maxContext, parsePositiveInt);
  if (!ctx.ok) return { error: "Max context must be a positive integer." };
  const inCost = parseOptionalField(fields.costInput, parseNonNegFloat);
  if (!inCost.ok) return { error: "Input cost must be a non-negative number." };
  const outCost = parseOptionalField(fields.costOutput, parseNonNegFloat);
  if (!outCost.ok)
    return { error: "Output cost must be a non-negative number." };
  const imageCost = parseOptionalField(fields.costImage, parseNonNegFloat);
  if (!imageCost.ok)
    return { error: "Cost per image must be a non-negative number." };
  const latency = parseOptionalField(fields.latencyMs, parsePositiveInt);
  if (!latency.ok)
    return { error: "Latency must be a positive integer (milliseconds)." };
  return {
    model: buildManualModel({
      idTrimmed,
      alias: fields.alias,
      ctx: ctx.value,
      inCost: inCost.value,
      outCost: outCost.value,
      imageCost: imageCost.value,
      latency: latency.value,
    }),
  };
}
