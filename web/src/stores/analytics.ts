import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as analyticsApi from '@/api/endpoints/analytics'
import { getErrorMessage } from '@/utils/errors'
import type { OverviewMetrics } from '@/api/types'

export const useAnalyticsStore = defineStore('analytics', () => {
  const metrics = ref<OverviewMetrics | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let fetchGeneration = 0

  async function fetchMetrics() {
    const gen = ++fetchGeneration
    loading.value = true
    error.value = null
    try {
      const result = await analyticsApi.getOverviewMetrics()
      // Only apply if this is still the latest fetch (prevents stale overwrites)
      if (gen === fetchGeneration) {
        metrics.value = result
      }
    } catch (err) {
      if (gen === fetchGeneration) {
        error.value = getErrorMessage(err)
      }
    } finally {
      if (gen === fetchGeneration) {
        loading.value = false
      }
    }
  }

  return { metrics, loading, error, fetchMetrics }
})
