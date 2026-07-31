import { useEffect, useMemo } from 'react'
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

/**
 * The one MODEL_REF setting whose binding no provider serves. Without it here
 * the picker lists connected providers only, so an operator who chose the
 * built-in during setup could never see it, change to it, or be warned about
 * it again afterwards.
 */
const EMBEDDER_SETTING_KEY = 'memory/embedder_model'

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

  return (
    <AgentModelPicker
      currentProvider={provider}
      currentModelId={modelId}
      providers={providerMap}
      onChange={(nextProvider, nextModelId) =>
        onChange(encodeModelRef(nextProvider, nextModelId))
      }
      disabled={disabled}
      hideLabel
      kind={isEmbedder ? 'embedding' : 'chat'}
      extraGroups={isEmbedder ? BUILTIN_EMBEDDER_GROUP : undefined}
      hint={fieldHint({ isEmbedder, provider, modelId })}
    />
  )
}
