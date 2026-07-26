import { useEffect, useMemo } from 'react'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import { useProvidersStore } from '@/stores/providers'
import { decodeModelRef, encodeModelRef } from '@/utils/model-ref'
import type { ProviderConfig } from '@/api/types/providers'

interface ModelRefFieldProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean | undefined
}

/**
 * Setting field for a ``MODEL_REF`` value: a provider -> model picker instead
 * of a free-text model string. The provider travels with the model so the
 * assignment resolves against the provider it was selected on, never the first
 * that happens to serve the id. Sources the live provider catalogue from the
 * providers store (hydrated on mount when empty).
 */
export function ModelRefField({ value, onChange, disabled }: ModelRefFieldProps) {
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
    />
  )
}
