import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as providersApi from '@/api/endpoints/providers'
import type { ProviderConfig } from '@/api/types'

export const useProviderStore = defineStore('providers', () => {
  const providers = ref<Record<string, ProviderConfig>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchProviders() {
    loading.value = true
    error.value = null
    try {
      providers.value = await providersApi.listProviders()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load providers'
    } finally {
      loading.value = false
    }
  }

  return { providers, loading, error, fetchProviders }
})
