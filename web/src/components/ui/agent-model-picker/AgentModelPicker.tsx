import { useMemo } from 'react'
import { SelectField } from '@/components/ui/select-field'
import type { SelectOption, SelectOptionGroup } from '@/components/ui/select-field'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

export interface AgentModelPickerProps {
  currentProvider: string
  currentModelId: string
  providers: Readonly<Record<string, ProviderConfig>>
  onChange: (provider: string, modelId: string) => void
  disabled?: boolean
}

const OTHER_FAMILY = 'Other'
const TOKENS_PER_K = 1000

interface ModelValue {
  provider: string
  modelId: string
}

/** Encode a provider/model pair as a collision-free option value. */
function encodeModelValue(provider: string, modelId: string): string {
  return JSON.stringify({ provider, modelId })
}

/** Narrow a parsed JSON value to a non-empty provider/model pair. */
function isModelValue(value: unknown): value is ModelValue {
  if (typeof value !== 'object' || value === null) return false
  const { provider, modelId } = value as Partial<ModelValue>
  return (
    typeof provider === 'string' &&
    typeof modelId === 'string' &&
    provider.length > 0 &&
    modelId.length > 0
  )
}

/** Decode an option value, or ``null`` when it is empty / malformed. */
function decodeModelValue(raw: string): ModelValue | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    return isModelValue(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Compact capability + context hint shown after a model's name. */
function modelHint(model: ProviderModelConfig): string {
  const parts: string[] = [`${Math.round(model.max_context / TOKENS_PER_K)}k`]
  if (model.metadata.supports_tools) parts.push('tools')
  if (model.metadata.supports_vision) parts.push('vision')
  if (model.stale != null) parts.push('stale')
  return parts.join(' · ')
}

function modelOption(providerName: string, model: ProviderModelConfig): SelectOption {
  return {
    value: encodeModelValue(providerName, model.id),
    label: `${model.alias ?? model.id} (${modelHint(model)})`,
  }
}

/**
 * Build provider -> family option groups. Each native ``<optgroup>`` is
 * labelled ``"<provider> · <family>"`` so the operator sees both the
 * provider and the model family; models with no parsed family fall under
 * ``"<provider> · Other"``.
 */
function buildModelGroups(
  providers: Readonly<Record<string, ProviderConfig>>,
): SelectOptionGroup[] {
  const groups: SelectOptionGroup[] = []
  for (const [providerName, config] of Object.entries(providers)) {
    const byFamily = new Map<string, SelectOption[]>()
    for (const model of config.models) {
      const family = model.metadata.family ?? OTHER_FAMILY
      const bucket = byFamily.get(family) ?? []
      bucket.push(modelOption(providerName, model))
      byFamily.set(family, bucket)
    }
    for (const [family, options] of byFamily) {
      groups.push({ label: `${providerName} · ${family}`, options })
    }
  }
  return groups
}

/**
 * Model picker grouped by provider and model family, surfacing context
 * size + capability hints per model. Shared between the setup wizard's
 * agent step and the post-setup agent model-change drawer.
 */
export function AgentModelPicker({
  currentProvider,
  currentModelId,
  providers,
  onChange,
  disabled,
}: AgentModelPickerProps) {
  const groups = useMemo(() => buildModelGroups(providers), [providers])
  const hasModels = groups.some((g) => g.options.length > 0)
  const currentValue =
    currentProvider && currentModelId
      ? encodeModelValue(currentProvider, currentModelId)
      : ''

  return (
    <SelectField
      label="Model"
      groups={groups}
      value={currentValue}
      onChange={(val) => {
        const parsed = decodeModelValue(val)
        if (parsed) onChange(parsed.provider, parsed.modelId)
      }}
      disabled={disabled}
      placeholder={hasModels ? 'Select model...' : 'No models available'}
    />
  )
}
