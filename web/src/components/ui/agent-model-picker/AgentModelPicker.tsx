import { useMemo } from 'react'
import { SelectField } from '@/components/ui/select-field'
import type { SelectOption, SelectOptionGroup } from '@/components/ui/select-field'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

export interface AgentModelPickerProps {
  currentProvider: string
  currentModelId: string
  providers: Readonly<Record<string, ProviderConfig>>
  onChange: (provider: string, modelId: string) => void
  disabled?: boolean | undefined
  /**
   * Accessible (and visible, unless hidden) field label. Row callers pass a
   * row-specific label like ``Model for ${agent.name}`` so a hidden label still
   * gives screen-reader users per-row context. Defaults to ``"Model"``.
   */
  label?: string | undefined
  /** Visually hide the label (e.g. inside a labelled table column). */
  hideLabel?: boolean | undefined
  /**
   * Option groups appended after the provider catalogue. For a binding that
   * a provider cannot serve (the built-in embedder needs no network), so it
   * is offered last rather than competing with real models for the first
   * slot in the list.
   */
  extraGroups?: readonly SelectOptionGroup[] | undefined
  /** Help text under the control, e.g. a warning about the current choice. */
  hint?: string | undefined
  /** Ids of external text describing the control. */
  describedBy?: string | undefined
  /**
   * Which half of the catalogue to offer. Defaults to `chat`.
   *
   * An embedding model returns vectors and cannot hold a conversation; a chat
   * model cannot produce an embedding. Listing the whole catalogue either way
   * offers every operator a set of choices that cannot work in the slot they
   * are filling, and the failure only surfaces at dispatch.
   */
  kind?: ModelKind | undefined
}

export type ModelKind = 'chat' | 'embedding'

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
  // Runtime feedback proved this model cannot call tools; show 'no tools'
  // instead of the static 'tools' claim so an operator does not pick it for
  // a tool-requiring agent (and never the contradictory 'tools · no tools').
  const toolCallsBroken = model.metadata.tool_calls_verified === false
  if (model.metadata.supports_tools && !toolCallsBroken) parts.push('tools')
  if (model.metadata.supports_vision) parts.push('vision')
  if (toolCallsBroken) parts.push('no tools')
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
  kind: ModelKind,
): SelectOptionGroup[] {
  const groups: SelectOptionGroup[] = []
  for (const [providerName, config] of Object.entries(providers)) {
    const byFamily = new Map<string, SelectOption[]>()
    for (const model of config.models) {
      if (model.metadata.supports_embeddings !== (kind === 'embedding')) continue
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
  label = 'Model',
  hideLabel,
  extraGroups,
  hint,
  describedBy,
  kind = 'chat',
}: AgentModelPickerProps) {
  const providerGroups = useMemo(
    () => buildModelGroups(providers, kind),
    [providers, kind],
  )
  const groups = useMemo(
    () => [...providerGroups, ...(extraGroups ?? [])],
    [providerGroups, extraGroups],
  )
  const hasModels = groups.some((g) => g.options.length > 0)
  // A model with no provider falls through as its bare id rather than as the
  // empty string, because the two are not the same thing and collapsing them
  // rendered a stored-but-unbindable value identically to nothing stored: the
  // row was badged MODIFIED while the control read "Select model...". Passed
  // on, it lands in SelectField's stale-value path, which shows the value and
  // says it cannot be used.
  const currentValue =
    currentProvider && currentModelId
      ? encodeModelValue(currentProvider, currentModelId)
      : currentModelId

  return (
    <SelectField
      label={label}
      hideLabel={hideLabel}
      groups={groups}
      value={currentValue}
      onChange={(val) => {
        const parsed = decodeModelValue(val)
        if (parsed) onChange(parsed.provider, parsed.modelId)
      }}
      disabled={disabled}
      placeholder={hasModels ? 'Select model...' : 'No models available'}
      hint={hint}
      describedBy={describedBy}
    />
  )
}
