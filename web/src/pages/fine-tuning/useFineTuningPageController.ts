import { useCallback, useEffect, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { ACTIVE_STAGES } from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'
import { useChannelHandler } from '@/hooks/useChannelHandler'
import {
  selectFineTuningBannerError,
  useFineTuningStore,
} from '@/stores/fine-tuning'
import { useWebSocketStore } from '@/stores/websocket'

export interface FineTuningPageController {
  status: ReturnType<typeof useFineTuningStore.getState>['status']
  checkpoints: ReturnType<typeof useFineTuningStore.getState>['checkpoints']
  runs: ReturnType<typeof useFineTuningStore.getState>['runs']
  bannerError: string | null
  hasDependencyFailure: boolean
  isActive: boolean
  isInitialLoading: boolean
  showEmptyState: boolean
}

export function useFineTuningPageController(): FineTuningPageController {
  const {
    status,
    preflight,
    checkpoints,
    runs,
    errors,
    fetchStatus,
    fetchCheckpoints,
    fetchRuns,
    handleWsEvent,
  } = useFineTuningStore(
    useShallow((s) => ({
      status: s.status,
      preflight: s.preflight,
      checkpoints: s.checkpoints,
      runs: s.runs,
      errors: s.errors,
      fetchStatus: s.fetchStatus,
      fetchCheckpoints: s.fetchCheckpoints,
      fetchRuns: s.fetchRuns,
      handleWsEvent: s.handleWsEvent,
    })),
  )
  const { subscribe, unsubscribe } = useWebSocketStore(
    useShallow((s) => ({ subscribe: s.subscribe, unsubscribe: s.unsubscribe })),
  )

  const [bootstrapComplete, setBootstrapComplete] = useState(false)
  useEffect(() => {
    let cancelled = false
    void Promise.allSettled([fetchStatus(), fetchCheckpoints(), fetchRuns()]).finally(
      () => {
        if (!cancelled) setBootstrapComplete(true)
      },
    )
    return () => {
      cancelled = true
    }
  }, [fetchStatus, fetchCheckpoints, fetchRuns])

  const wsHandler = useCallback(
    (event: WsEvent) => {
      handleWsEvent(event)
    },
    [handleWsEvent],
  )
  useChannelHandler('system', wsHandler)

  useEffect(() => {
    subscribe(['system'])
    return () => {
      unsubscribe(['system'])
    }
  }, [subscribe, unsubscribe])

  const isActive = status != null && ACTIVE_STAGES.has(status.stage)
  const hasDependencyFailure = computeDependencyFailure(preflight)
  // First render after mount, before any fetch has settled: show skeleton.
  // Keyed on `bootstrapComplete` rather than empty-store inference so a
  // failed initial fetch surfaces the empty state instead of hanging in
  // skeleton mode.
  const isInitialLoading =
    !bootstrapComplete && checkpoints.length === 0 && runs.length === 0
  // After the bootstrap has completed but the pipeline has never produced
  // checkpoints or runs, show an empty state instead of two tables full of
  // placeholder rows.
  const showEmptyState =
    bootstrapComplete && !isActive && checkpoints.length === 0 && runs.length === 0

  return {
    status,
    checkpoints,
    runs,
    bannerError: selectFineTuningBannerError(errors),
    hasDependencyFailure,
    isActive,
    isInitialLoading,
    showEmptyState,
  }
}

function computeDependencyFailure(
  preflight: ReturnType<typeof useFineTuningStore.getState>['preflight'],
): boolean {
  if (preflight == null) return false
  return preflight.checks.some((c) => c.name === 'dependencies' && c.status === 'fail')
}
