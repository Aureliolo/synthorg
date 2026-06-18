import { Dialog } from "@base-ui/react/dialog";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { InputField } from "@/components/ui/input-field";
import { useProvidersStore } from "@/stores/providers";
import { useSettingsStore } from "@/stores/settings";
import type { ProviderModelConfig } from "@/api/types/providers";

interface AddManualModelDialogProps {
  providerName: string | null;
  open: boolean;
  onClose: () => void;
}

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

interface ManualModelInputs {
  modelId: string;
  alias: string;
  costInput: string;
  costOutput: string;
  maxContext: string;
  latencyMs: string;
}

interface ParsedModelValues {
  idTrimmed: string;
  alias: string;
  ctx: number | null;
  inCost: number | null;
  outCost: number | null;
  latency: number | null;
}

function buildManualModel(v: ParsedModelValues): ProviderModelConfig {
  return {
    id: v.idTrimmed,
    alias: v.alias.trim() || null,
    cost_per_1k_input: v.inCost ?? 0,
    cost_per_1k_output: v.outCost ?? 0,
    max_context: v.ctx ?? 200_000,
    estimated_latency_ms: v.latency ?? null,
    local_params: null,
    // Manually-added models are unenriched; backend enriches on next sync.
    metadata: {
      supports_tools: false,
      supports_vision: false,
      supports_reasoning: false,
      max_output_tokens: null,
      family: null,
      generation: null,
      release_date: null,
      metadata_source: "unknown",
    },
    stale: null,
  };
}

type ManualModelValidation = { error: string } | { model: ProviderModelConfig };

function validateManualModel(fields: ManualModelInputs): ManualModelValidation {
  const idTrimmed = fields.modelId.trim();
  if (idTrimmed === "") return { error: "Model id is required." };
  const ctx = parseOptionalField(fields.maxContext, parsePositiveInt);
  if (!ctx.ok) return { error: "Max context must be a positive integer." };
  const inCost = parseOptionalField(fields.costInput, parseNonNegFloat);
  if (!inCost.ok) return { error: "Input cost must be a non-negative number." };
  const outCost = parseOptionalField(fields.costOutput, parseNonNegFloat);
  if (!outCost.ok)
    return { error: "Output cost must be a non-negative number." };
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
      latency: latency.value,
    }),
  };
}

interface ManualModelForm {
  values: ManualModelInputs;
  setModelId: (value: string) => void;
  setAlias: (value: string) => void;
  setCostInput: (value: string) => void;
  setCostOutput: (value: string) => void;
  setMaxContext: (value: string) => void;
  setLatencyMs: (value: string) => void;
  submitting: boolean;
  setSubmitting: (value: boolean) => void;
  validationError: string | null;
  setValidationError: (value: string | null) => void;
  reset: () => void;
}

function useManualModelForm(): ManualModelForm {
  const [modelId, setModelId] = useState("");
  const [alias, setAlias] = useState("");
  const [costInput, setCostInput] = useState("");
  const [costOutput, setCostOutput] = useState("");
  const [maxContext, setMaxContext] = useState("");
  const [latencyMs, setLatencyMs] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const reset = (): void => {
    setModelId("");
    setAlias("");
    setCostInput("");
    setCostOutput("");
    setMaxContext("");
    setLatencyMs("");
    setSubmitting(false);
    setValidationError(null);
  };

  return {
    values: { modelId, alias, costInput, costOutput, maxContext, latencyMs },
    setModelId,
    setAlias,
    setCostInput,
    setCostOutput,
    setMaxContext,
    setLatencyMs,
    submitting,
    setSubmitting,
    validationError,
    setValidationError,
    reset,
  };
}

function ManualModelFields({
  form,
  currency,
}: {
  form: ManualModelForm;
  currency: string;
}) {
  const { values } = form;
  return (
    <div className="mt-section-gap flex flex-col gap-grid-gap">
      <InputField
        label="Model id"
        hint="The exact identifier the provider's API expects"
        value={values.modelId}
        onChange={(e) => form.setModelId(e.target.value)}
        required
      />
      <InputField
        label="Alias"
        hint="Optional shorthand for routing rules"
        value={values.alias}
        onChange={(e) => form.setAlias(e.target.value)}
      />
      <div className="grid grid-cols-2 gap-grid-gap">
        <InputField
          label="Cost / 1k input tokens"
          hint={`${currency}; leave blank for 0`}
          type="number"
          inputMode="decimal"
          value={values.costInput}
          onChange={(e) => form.setCostInput(e.target.value)}
          min={0}
          step="0.0001"
        />
        <InputField
          label="Cost / 1k output tokens"
          hint={`${currency}; leave blank for 0`}
          type="number"
          inputMode="decimal"
          value={values.costOutput}
          onChange={(e) => form.setCostOutput(e.target.value)}
          min={0}
          step="0.0001"
        />
      </div>
      <div className="grid grid-cols-2 gap-grid-gap">
        <InputField
          label="Max context (tokens)"
          hint="Defaults to 200k"
          type="number"
          inputMode="numeric"
          value={values.maxContext}
          onChange={(e) => form.setMaxContext(e.target.value)}
          min={1}
        />
        <InputField
          label="Latency (ms)"
          hint="Optional; used by fastest routing"
          type="number"
          inputMode="numeric"
          value={values.latencyMs}
          onChange={(e) => form.setLatencyMs(e.target.value)}
          min={1}
        />
      </div>
    </div>
  );
}

/**
 * Modal for the manual model add flow.  Bypasses discovery; the
 * operator types in the model id and pricing.  Conflict (model id
 * already exists) becomes an error toast surfaced by the store.
 */
export function AddManualModelDialog({
  providerName,
  open,
  onClose,
}: AddManualModelDialogProps) {
  const addProviderModel = useProvidersStore((s) => s.addProviderModel);
  const currency = useSettingsStore((s) => s.currency);
  // Track open-state in a ref so a slow add-model request that
  // resolves after the dialog closes does not wipe new-session
  // inputs by triggering ``reset()`` + ``onClose()`` on the new
  // form instance (mirrors SyncModelsConfirmDialog).
  const openRef = useRef(open);
  useEffect(() => {
    openRef.current = open;
  }, [open]);
  // Session token bumped on every close so a request that resolves after
  // a close+reopen cannot drive ``handleClose`` on the fresh session
  // (``openRef`` alone is ``true`` again and would not catch this).
  const dialogSessionRef = useRef(0);

  const form = useManualModelForm();

  const handleClose = (): void => {
    dialogSessionRef.current += 1;
    form.reset();
    onClose();
  };

  const handleSubmit = async (): Promise<void> => {
    if (!providerName) return;
    const sessionAtSubmit = dialogSessionRef.current;
    const result = validateManualModel(form.values);
    if ("error" in result) {
      form.setValidationError(result.error);
      return;
    }
    form.setValidationError(null);
    form.setSubmitting(true);
    const added = await addProviderModel(providerName, { model: result.model });
    if (!openRef.current || sessionAtSubmit !== dialogSessionRef.current) return;
    form.setSubmitting(false);
    if (added !== null) {
      handleClose();
    }
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) handleClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 bg-overlay backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-popup w-full max-w-lg md:max-w-2xl -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card p-card-tight sm:p-card md:p-card-roomy shadow-card-hover">
          <Dialog.Title className="text-lg font-semibold text-foreground">
            Add model manually
          </Dialog.Title>
          <Dialog.Description className="text-sm text-text-secondary">
            Use this when the model is not in the LiteLLM catalog and discovery
            does not surface it. Pricing fields are optional; leave them blank
            for free or unknown.
          </Dialog.Description>

          {form.validationError && (
            <div className="mt-section-gap">
              <ErrorBanner severity="warning" title={form.validationError} />
            </div>
          )}

          <ManualModelFields form={form} currency={currency} />

          <div className="mt-section-gap flex justify-end gap-grid-gap">
            <Button
              variant="secondary"
              onClick={handleClose}
              disabled={form.submitting}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleSubmit()}
              disabled={form.submitting}
            >
              {form.submitting ? "Adding…" : "Add model"}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
