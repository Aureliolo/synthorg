import { useState } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useProvidersStore } from '@/stores/providers'
import type {
  CapabilityOverridesUpdateRequest,
  LocalModelParams,
  ModelCapabilityOverrides,
  ProviderModelResponse,
} from '@/api/types/providers'

const STALE_REASON_LABEL: Record<'removed_from_catalog' | 'deprecated', string> = {
  removed_from_catalog: 'removed from the provider catalogue',
  deprecated: 'deprecated by the provider',
}

interface ModelConfigDrawerProps {
  providerName: string
  model: ProviderModelResponse | null
  open: boolean
  onClose: () => void
  /**
   * Whether the provider supports local launch-parameter tuning
   * (``supports_model_config``). Capability overrides apply to any
   * provider and are always shown; this only gates the local-params
   * section, which is meaningless for a provider with no local runtime.
   */
  supportsLocalParams: boolean
}

const EMPTY_LOCAL_PARAMS: LocalModelParams = {
  num_ctx: null,
  num_gpu_layers: null,
  num_threads: null,
  num_batch: null,
  repeat_penalty: null,
}

function numToInput(value: number | null): string {
  return value === null ? '' : String(value)
}

function parseIntStrict(val: string): number | null {
  const s = val.trim()
  if (!s) return null
  const n = Number(s)
  return Number.isFinite(n) && Number.isInteger(n) ? n : null
}

function parseFloatStrict(val: string): number | null {
  const s = val.trim()
  if (!s) return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

interface ModelConfigInputs {
  numCtx: string
  numGpuLayers: string
  numThreads: string
  numBatch: string
  repeatPenalty: string
}

function initialModelParams(params: LocalModelParams | null): ModelConfigInputs {
  const p = params ?? EMPTY_LOCAL_PARAMS
  return {
    numCtx: numToInput(p.num_ctx),
    numGpuLayers: numToInput(p.num_gpu_layers),
    numThreads: numToInput(p.num_threads),
    numBatch: numToInput(p.num_batch),
    repeatPenalty: numToInput(p.repeat_penalty),
  }
}

function buildLocalParams(inputs: ModelConfigInputs): LocalModelParams {
  return {
    num_ctx: parseIntStrict(inputs.numCtx),
    num_gpu_layers: parseIntStrict(inputs.numGpuLayers),
    num_threads: parseIntStrict(inputs.numThreads),
    num_batch: parseIntStrict(inputs.numBatch),
    repeat_penalty: parseFloatStrict(inputs.repeatPenalty),
  }
}

interface ModelConfigFieldsProps {
  numCtx: string
  setNumCtx: (value: string) => void
  numGpuLayers: string
  setNumGpuLayers: (value: string) => void
  numThreads: string
  setNumThreads: (value: string) => void
  numBatch: string
  setNumBatch: (value: string) => void
  repeatPenalty: string
  setRepeatPenalty: (value: string) => void
}

function ModelConfigFields({
  numCtx,
  setNumCtx,
  numGpuLayers,
  setNumGpuLayers,
  numThreads,
  setNumThreads,
  numBatch,
  setNumBatch,
  repeatPenalty,
  setRepeatPenalty,
}: ModelConfigFieldsProps) {
  return (
    <>
      <InputField
        label="Context window (num_ctx)"
        value={numCtx}
        onValueChange={setNumCtx}
        placeholder="e.g. 4096"
        hint="Context window size in tokens"
      />
      <InputField
        label="GPU layers (num_gpu_layers)"
        value={numGpuLayers}
        onValueChange={setNumGpuLayers}
        placeholder="e.g. 32"
        hint="Number of layers to offload to GPU (0 = CPU only)"
      />
      <InputField
        label="CPU threads (num_threads)"
        value={numThreads}
        onValueChange={setNumThreads}
        placeholder="auto"
        hint="Number of CPU threads"
      />
      <InputField
        label="Batch size (num_batch)"
        value={numBatch}
        onValueChange={setNumBatch}
        placeholder="512"
        hint="Batch size for prompt processing"
      />
      <InputField
        label="Repetition penalty (repeat_penalty)"
        value={repeatPenalty}
        onValueChange={setRepeatPenalty}
        placeholder="1.1"
        hint="Penalize repeated tokens (1.0 = disabled)"
      />
    </>
  )
}

type OverrideField = keyof ModelCapabilityOverrides

interface OverrideFieldSpec {
  field: OverrideField
  label: string
  resolved: (model: ProviderModelResponse) => boolean
}

const OVERRIDE_FIELDS: readonly OverrideFieldSpec[] = [
  { field: 'supports_tools', label: 'Tool calling', resolved: (m) => m.supports_tools },
  { field: 'supports_vision', label: 'Vision', resolved: (m) => m.supports_vision },
  { field: 'supports_streaming', label: 'Streaming', resolved: (m) => m.supports_streaming },
  { field: 'supports_embeddings', label: 'Embeddings', resolved: (m) => m.supports_embeddings },
  {
    field: 'supports_image_generation',
    label: 'Image generation',
    resolved: (m) => m.supports_image_generation,
  },
  { field: 'supports_reasoning', label: 'Reasoning', resolved: (m) => m.supports_reasoning },
  {
    field: 'supports_prompt_caching',
    label: 'Prompt caching',
    resolved: (m) => m.supports_prompt_caching,
  },
]

const OVERRIDE_OPTIONS: readonly SelectOption[] = [
  { value: '', label: 'Auto (resolved value)' },
  { value: 'true', label: 'Force on' },
  { value: 'false', label: 'Force off' },
]

/** Three-state override value as a `<select>` string: '', 'true', or 'false'. */
function overrideToSelectValue(value: boolean | null | undefined): string {
  if (value === true) return 'true'
  if (value === false) return 'false'
  return ''
}

function selectValueToOverride(value: string): boolean | null {
  if (value === 'true') return true
  if (value === 'false') return false
  return null
}

function initialOverrideSelections(
  overrides: ModelCapabilityOverrides | null,
): Record<OverrideField, string> {
  return Object.fromEntries(
    OVERRIDE_FIELDS.map((spec) => [
      spec.field,
      overrideToSelectValue(overrides?.[spec.field]),
    ]),
  ) as Record<OverrideField, string>
}

function buildOverridesRequest(
  selections: Record<OverrideField, string>,
): CapabilityOverridesUpdateRequest {
  const capabilityFields = Object.fromEntries(
    OVERRIDE_FIELDS.map((spec) => [spec.field, selectValueToOverride(selections[spec.field])]),
  )
  // ``confirm``/``reason`` are the deliberate-action ceremony for one governed
  // transition (forcing vision onto the vision-verify-gate model); every other
  // save is unguarded and the backend ignores them. A rejected save re-sends
  // this same shape with both filled in via ``confirmPendingCapabilityOverrides``.
  return { ...capabilityFields, confirm: false, reason: '' }
}

function CapabilityOverridesForm({
  providerName,
  model,
}: {
  providerName: string
  model: ProviderModelResponse
}) {
  const updateOverrides = useProvidersStore((s) => s.updateModelCapabilityOverrides)
  const saving = useProvidersStore((s) => s.updatingCapabilityOverrides)
  const [selections, setSelections] = useState(() =>
    initialOverrideSelections(model.capability_overrides),
  )

  const handleSave = async () => {
    await updateOverrides(providerName, model.id, buildOverridesRequest(selections))
  }

  return (
    <div className="flex flex-col gap-section-gap border-t border-border pt-section-gap">
      <h3 className="text-sm font-medium text-foreground">Capability overrides</h3>
      <p className="text-xs text-text-muted">
        Force a capability on or off when the model card is silent on it. An override wins over
        whatever the provider or LiteLLM reports.
      </p>
      {OVERRIDE_FIELDS.map((spec) => (
        <SelectField
          key={spec.field}
          label={`${spec.label} (resolved: ${spec.resolved(model) ? 'on' : 'off'})`}
          value={selections[spec.field]}
          options={OVERRIDE_OPTIONS}
          disabled={saving}
          onChange={(value) => setSelections((prev) => ({ ...prev, [spec.field]: value }))}
        />
      ))}
      <div className="flex justify-end pt-2">
        <Button size="sm" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save overrides'}
        </Button>
      </div>
    </div>
  )
}

function LocalParamsForm({
  providerName,
  model,
  onClose,
}: {
  providerName: string
  model: ProviderModelResponse
  onClose: () => void
}) {
  const updateModelConfig = useProvidersStore((s) => s.updateModelConfig)
  const saving = useProvidersStore((s) => s.updatingModelConfig)
  const initial = initialModelParams(model.local_params)

  const [numCtx, setNumCtx] = useState(initial.numCtx)
  const [numGpuLayers, setNumGpuLayers] = useState(initial.numGpuLayers)
  const [numThreads, setNumThreads] = useState(initial.numThreads)
  const [numBatch, setNumBatch] = useState(initial.numBatch)
  const [repeatPenalty, setRepeatPenalty] = useState(initial.repeatPenalty)

  const handleSave = async () => {
    const newParams = buildLocalParams({ numCtx, numGpuLayers, numThreads, numBatch, repeatPenalty })
    const success = await updateModelConfig(providerName, model.id, newParams)
    if (success) onClose()
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <ModelConfigFields
        numCtx={numCtx}
        setNumCtx={setNumCtx}
        numGpuLayers={numGpuLayers}
        setNumGpuLayers={setNumGpuLayers}
        numThreads={numThreads}
        setNumThreads={setNumThreads}
        numBatch={numBatch}
        setNumBatch={setNumBatch}
        repeatPenalty={repeatPenalty}
        setRepeatPenalty={setRepeatPenalty}
      />
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

function ModelConfigDrawerBody({
  providerName,
  model,
  onClose,
  supportsLocalParams,
}: {
  providerName: string
  model: ProviderModelResponse
  onClose: () => void
  supportsLocalParams: boolean
}) {
  return (
    <div className="flex flex-col gap-section-gap">
      {model.stale != null && (
        <ErrorBanner
          variant="section"
          severity="warning"
          title="This model is stale"
          description={`${model.id} was ${STALE_REASON_LABEL[model.stale.reason]}.${
            model.stale.successor_model_id != null
              ? ` Consider switching to ${model.stale.successor_model_id}.`
              : ' Consider switching to a current model.'
          }`}
        />
      )}
      {supportsLocalParams && (
        <LocalParamsForm providerName={providerName} model={model} onClose={onClose} />
      )}
      <CapabilityOverridesForm providerName={providerName} model={model} />
    </div>
  )
}

export function ModelConfigDrawer({
  providerName,
  model,
  open,
  onClose,
  supportsLocalParams,
}: ModelConfigDrawerProps) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={model ? `Configure ${model.id}` : 'Configure Model'}
    >
      {model && (
        <ModelConfigDrawerBody
          key={model.id}
          providerName={providerName}
          model={model}
          onClose={onClose}
          supportsLocalParams={supportsLocalParams}
        />
      )}
    </Drawer>
  )
}
