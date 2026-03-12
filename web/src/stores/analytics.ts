import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as analyticsApi from '@/api/endpoints/analytics'
import type { OverviewMetrics } from '@/api/types'

export const useAnalyticsStore = defineStore('analytics', () => {
  const metrics = ref<OverviewMetrics | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchMetrics() {
    loading.value = true
    error.value = null
    try {
      metrics.value = await analyticsApi.getOverviewMetrics()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load metrics'
    } finally {
      loading.value = false
    }
  }

  return { metrics, loading, error, fetchMetrics }
})
