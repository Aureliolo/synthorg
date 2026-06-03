import { useCallback, useEffect } from 'react'
import { useProvidersStore } from '@/stores/providers'
import { usePolling } from '@/hooks/usePolling'
import type {
  ProviderHealthSummary,
  ProviderModelResponse,
  TestConnectionResponse,
} from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

const DETAIL_POLL_INTERVAL = 30_000

export interface UseProviderDetailDataReturn {
  provider: ProviderWithName | null
  models: readonly ProviderModelResponse[]
  health: ProviderHealthSummary | null
  loading: boolean
  error: string | null
  testConnectionResult: TestConnectionResponse | null
  testingConnection: boolean
}

export function useProviderDetailData(
  providerName: string,
): UseProviderDetailDataReturn {
  const provider = useProvidersStore((s) => s.selectedProvider)
  const models = useProvidersStore((s) => s.selectedProviderModels)
  const health = useProvidersStore((s) => s.selectedProviderHealth)
  const loading = useProvidersStore((s) => s.detailLoading)
  const error = useProvidersStore((s) => s.detailError)
  const testConnectionResult = useProvidersStore(
    (s) => s.testConnectionResult,
  )
  const testingConnection = useProvidersStore((s) => s.testingConnection)

  // Cleanup on unmount or provider change
  useEffect(() => {
    if (!providerName) {
      useProvidersStore.getState().clearDetail()
    }
    return () => {
      useProvidersStore.getState().clearDetail()
    }
  }, [providerName])

  // Polling (start() fires immediately, so no separate initial fetch needed)
  const pollFn = useCallback(async () => {
    if (!providerName) return
    await useProvidersStore.getState().fetchProviderDetail(providerName)
  }, [providerName])
  const polling = usePolling(pollFn, DETAIL_POLL_INTERVAL)

  const { start, stop } = polling
  useEffect(() => {
    if (!providerName) return
    start()
    return () => stop()
  }, [providerName, start, stop])

  if (!providerName) {
    return {
      provider: null,
      models: [],
      health: null,
      loading: false,
      error: null,
      testConnectionResult: null,
      testingConnection: false,
    }
  }

  return {
    provider,
    models,
    health,
    loading,
    error,
    testConnectionResult,
    testingConnection,
  }
}
