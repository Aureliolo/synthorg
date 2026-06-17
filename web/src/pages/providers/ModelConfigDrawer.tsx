import { useState } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useProvidersStore } from '@/stores/providers'
import type { LocalModelParams, ProviderModelResponse } from '@/api/types/providers'

const STALE_REASON_LABEL: Record<'removed_from_catalog' | 'deprecated', string> = {
  removed_from_catalog: 'removed from the provider catalogue',
  deprecated: 'deprecated by the provider',
}

interface ModelConfigDrawerProps {
  providerName: string
  model: ProviderModelResponse | null
  open: boolean
  onClose: () => void
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

function ModelConfigForm({
  providerName,
  model,
  onClose,
}: {
  providerName: string
  model: ProviderModelResponse
  onClose: () => void
}) {
  const updateModelConfig = useProvidersStore((s) => s.updateModelConfig)
  const initial = initialModelParams(model.local_params)

  const [numCtx, setNumCtx] = useState(initial.numCtx)
  const [numGpuLayers, setNumGpuLayers] = useState(initial.numGpuLayers)
  const [numThreads, setNumThreads] = useState(initial.numThreads)
  const [numBatch, setNumBatch] = useState(initial.numBatch)
  const [repeatPenalty, setRepeatPenalty] = useState(initial.repeatPenalty)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    const newParams = buildLocalParams({ numCtx, numGpuLayers, numThreads, numBatch, repeatPenalty })
    const success = await updateModelConfig(providerName, model.id, newParams)
    setSaving(false)
    if (success) onClose()
  }

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

export function ModelConfigDrawer({ providerName, model, open, onClose }: ModelConfigDrawerProps) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={model ? `Configure ${model.id}` : 'Configure Model'}
    >
      {model && (
        <ModelConfigForm
          key={model.id}
          providerName={providerName}
          model={model}
          onClose={onClose}
        />
      )}
    </Drawer>
  )
}
