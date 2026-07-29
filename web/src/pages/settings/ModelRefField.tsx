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
      extraGroups={isEmbedder ? BUILTIN_EMBEDDER_GROUP : undefined}
      hint={
        isEmbedder && isBuiltinEmbedderProvider(provider)
          ? BUILTIN_EMBEDDER_HINT
          : undefined
      }
    />
  )
}
