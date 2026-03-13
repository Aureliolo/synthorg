import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as providersApi from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import type { ProviderConfig } from '@/api/types'

const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])

/** Strip any accidentally-serialized secrets before storing in reactive state. */
function sanitizeProviders(raw: Record<string, ProviderConfig>): Record<string, ProviderConfig> {
  const result = Object.create(null) as Record<string, ProviderConfig>
  for (const [key, provider] of Object.entries(raw)) {
    if (UNSAFE_KEYS.has(key)) continue
    // Destructure to omit api_key if the backend accidentally includes it
    const { api_key: _discarded, ...safe } = provider as ProviderConfig & { api_key?: unknown }
    result[key] = safe
  }
  return result
}

export const useProviderStore = defineStore('providers', () => {
  const providers = ref<Record<string, ProviderConfig>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchProviders() {
    loading.value = true
    error.value = null
    try {
      const raw = await providersApi.listProviders()
      providers.value = sanitizeProviders(raw)
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  return { providers, loading, error, fetchProviders }
})
