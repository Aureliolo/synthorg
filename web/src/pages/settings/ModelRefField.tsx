import { useCallback, useEffect, useMemo } from 'react'
import type { EmbedderProbeResponse } from '@/api/types/system'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import type { SelectOptionGroup } from '@/components/ui/select-field'
import { useProvidersStore } from '@/stores/providers'
import {
  BUILTIN_EMBEDDER_HINT,
  BUILTIN_EMBEDDER_LABEL,
  BUILTIN_EMBEDDER_MODEL,
  BUILTIN_EMBEDDER_PROVIDER,
  isBuiltinEmbedderProvider,
} from '@/utils/builtin-embedder'
import { decodeModelRef, encodeModelRef } from '@/utils/model-ref'
import type { ProviderConfig } from '@/api/types/providers'
import { useEmbedderProbe } from './useEmbedderProbe'

/**
 * The one MODEL_REF setting whose binding no provider serves. Without it here
 * the picker lists connected providers only, so an operator who chose the
 * built-in during setup could never see it, change to it, or be warned about
 * it again afterwards.
 */
const EMBEDDER_SETTING_KEY = 'memory/embedder_model'

// Says that a wait is expected rather than leaving a spinner to read as a
// hang: a model pulled moments ago has to be loaded before it can answer,
// which took 16 seconds locally against a fraction of a second once warm.
const PROBING_HINT =
  'Measuring the vector width... a model that was just pulled has to load first.'

// The measurement failing is not the same as a model being unusable, and the
// commonest cause is the one the operator can simply retry past.
const PROBE_FAILED_HINT =
  'Could not measure the vector width: the model did not answer in time. ' +
  'Select it again to retry; a first load can outlast the probe deadline.'

const BUILTIN_EMBEDDER_GROUP: readonly SelectOptionGroup[] = [
  {
    label: 'No embedding model',
    options: [
      {
        value: JSON.stringify({
          provider: BUILTIN_EMBEDDER_PROVIDER,
          modelId: BUILTIN_EMBEDDER_MODEL,
        }),
        label: BUILTIN_EMBEDDER_LABEL,
      },
    ],
  },
]

/**
 * What a measured width means for the store, in the operator's terms.
 *
 * States the mechanical consequence and stops: which embedder to run is the
 * operator's choice, and the memory design is explicit that nothing ranks or
 * recommends one for them.
 *
 * @returns The sentence to show under the picker.
 */
function widthVerdict(probe: EmbedderProbeResponse): string {
  const width = `${probe.dims} dimensions`
  if (probe.index_support === 'indexed') {
    return `${width}: indexed, so recall stays fast as memory grows.`
  }
  if (probe.index_support === 'indexed_half_precision') {
    return (
      `${width}: indexed at half precision, because full precision indexes ` +
      `stop at ${probe.vector_ceiling}. Slightly approximate, still fast.`
    )
  }
  if (probe.index_support === 'exact_scan') {
    return (
      `${width}: too wide to index (the ceiling is ${probe.halfvec_ceiling}), ` +
      'so every search reads every stored memory. Correct, but slower the ' +
      'more the organisation remembers.'
    )
  }
  return `${width}: wider than this store can hold at all.`
}

/**
 * Help text for the current state of the binding.
 *
 * The unbound case earns its own sentence because the generic stale-value note
 * says only that the value is unavailable, which reads as a model that went
 * away. A bare model id is a different fault: the model may well be there, but
 * the reference names no provider, so nothing can resolve it and the value is
 * refused at write time. An operator told "unavailable" would go looking for a
 * missing model.
 *
 * @returns The hint to render, or `undefined` when the binding needs no note.
 */
function fieldHint({
  isEmbedder,
  provider,
  modelId,
}: {
  isEmbedder: boolean
  provider: string
  modelId: string
}): string | undefined {
  if (modelId !== '' && provider === '') {
    return (
      `Stored as "${modelId}" with no provider, so nothing can resolve it and ` +
      'memory stays off. Pick the model under the provider that serves it.'
    )
  }
  if (isEmbedder && isBuiltinEmbedderProvider(provider)) return BUILTIN_EMBEDDER_HINT
  return undefined
}

interface ModelRefFieldProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean | undefined
  /** ``namespace/key``, so the embedder setting can offer the built-in. */
  settingKey?: string | undefined
}

/**
 * Setting field for a ``MODEL_REF`` value: a provider -> model picker instead
 * of a free-text model string. The provider travels with the model so the
 * assignment resolves against the provider it was selected on, never the first
 * that happens to serve the id. Sources the live provider catalogue from the
 * providers store (hydrated on mount when empty).
 */
export function ModelRefField({
  value,
  onChange,
  disabled,
  settingKey,
}: ModelRefFieldProps) {
  const providers = useProvidersStore((s) => s.providers)
  const listLoading = useProvidersStore((s) => s.listLoading)
  const fetchProviders = useProvidersStore((s) => s.fetchProviders)

  useEffect(() => {
    if (providers.length === 0 && !listLoading) void fetchProviders()
  }, [providers.length, listLoading, fetchProviders])

  const providerMap = useMemo<Readonly<Record<string, ProviderConfig>>>(
    () => Object.fromEntries(providers.map((p) => [p.name, p] as const)),
    [providers],
  )

  const { provider, modelId } = useMemo(() => decodeModelRef(value), [value])
  const isEmbedder = settingKey === EMBEDDER_SETTING_KEY
  const { probe, error: probeError, probing, start } = useEmbedderProbe(
    PROBE_FAILED_HINT,
    value,
  )

  const handleChange = useCallback(
    (nextProvider: string, nextModelId: string) => {
      const next = encodeModelRef(nextProvider, nextModelId)
      onChange(next)
      const measurable = isEmbedder && !isBuiltinEmbedderProvider(nextProvider)
      start(next, measurable ? { provider: nextProvider, modelId: nextModelId } : null)
    },
    [onChange, isEmbedder, start],
  )

  return (
    <AgentModelPicker
      currentProvider={provider}
      currentModelId={modelId}
      providers={providerMap}
      onChange={handleChange}
      disabled={disabled}
      hideLabel
      kind={isEmbedder ? 'embedding' : 'chat'}
      extraGroups={isEmbedder ? BUILTIN_EMBEDDER_GROUP : undefined}
      hint={
        probing
          ? PROBING_HINT
          : (probeError
              ?? (probe !== null
                    ? widthVerdict(probe)
                    : fieldHint({ isEmbedder, provider, modelId })))
      }
    />
  )
}
