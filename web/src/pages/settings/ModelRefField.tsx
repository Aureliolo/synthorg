import { useEffect, useMemo } from 'react'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import { createLogger } from '@/lib/logger'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderConfig } from '@/api/types/providers'

const log = createLogger('settings')

interface ModelRefValue {
  provider: string
  modelId: string
}

/**
 * Decode a stored ``MODEL_REF`` setting value into a provider/model pair.
 *
 * Accepts the canonical ``{provider, model_id}`` JSON the picker writes and a
 * legacy bare model string (read as model-only, provider empty, so the picker
 * prompts for an explicit provider selection).
 */
function decodeModelRef(value: string): ModelRefValue {
  const text = value.trim()
  if (!text) return { provider: '', modelId: '' }
  if (text.startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(text)
      if (typeof parsed === 'object' && parsed !== null) {
        const { provider, model_id: modelId } = parsed as Record<string, unknown>
        return {
          provider: typeof provider === 'string' ? provider : '',
          modelId: typeof modelId === 'string' ? modelId : '',
        }
      }
    } catch (err) {
      log.warn('decodeModelRef: value is not valid JSON, treating as model-only', err)
    }
  }
  return { provider: '', modelId: value }
}

/** Serialize a provider/model pair to the canonical stored ``MODEL_REF`` JSON. */
function encodeModelRef(provider: string, modelId: string): string {
  return JSON.stringify({ provider, model_id: modelId })
}

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
