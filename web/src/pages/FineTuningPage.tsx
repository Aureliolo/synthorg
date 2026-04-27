import { useCallback, useEffect, useState } from 'react'
import { Activity, Clock, Database, Settings } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'

import { ACTIVE_STAGES } from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useFineTuningStore } from '@/stores/fine-tuning'
import { useWebSocketStore } from '@/stores/websocket'

import { CheckpointTable } from './fine-tuning/CheckpointTable'
import { DependencyMissingBanner } from './fine-tuning/DependencyMissingBanner'
import { PipelineControlPanel } from './fine-tuning/PipelineControlPanel'
import { PipelineProgressBar } from './fine-tuning/PipelineProgressBar'
import { PipelineStepper } from './fine-tuning/PipelineStepper'
import { RunHistoryTable } from './fine-tuning/RunHistoryTable'

export default function FineTuningPage() {
  const {
    status,
    preflight,
    checkpoints,
    runs,
    fetchStatus,
    fetchCheckpoints,
    fetchRuns,
    handleWsEvent,
  } = useFineTuningStore(useShallow((s) => ({
    status: s.status,
    preflight: s.preflight,
    checkpoints: s.checkpoints,
    runs: s.runs,
    fetchStatus: s.fetchStatus,
    fetchCheckpoints: s.fetchCheckpoints,
    fetchRuns: s.fetchRuns,
    handleWsEvent: s.handleWsEvent,
  })))
  const { onChannelEvent, offChannelEvent, subscribe, unsubscribe } =
    useWebSocketStore(useShallow((s) => ({
      onChannelEvent: s.onChannelEvent,
      offChannelEvent: s.offChannelEvent,
      subscribe: s.subscribe,
      unsubscribe: s.unsubscribe,
    })))

  // Explicit bootstrap flag so a failed initial fetch does not leave the
  // page stuck in the skeleton state. The `!bootstrapComplete` guard is
  // what isInitialLoading below keys on; without it, an empty store after a
  // failed request would render skeletons forever.
  const [bootstrapComplete, setBootstrapComplete] = useState(false)
  useEffect(() => {
    let cancelled = false
    void Promise.allSettled([
      fetchStatus(),
      fetchCheckpoints(),
      fetchRuns(),
    ]).finally(() => {
      if (!cancelled) setBootstrapComplete(true)
    })
    return () => {
      cancelled = true
    }
  }, [fetchStatus, fetchCheckpoints, fetchRuns])

  // Subscribe to WebSocket events for real-time updates.
  const wsHandler = useCallback(
    (event: WsEvent) => {
      handleWsEvent(event)
    },
    [handleWsEvent],
  )

  useEffect(() => {
    subscribe(['system'])
    onChannelEvent('system', wsHandler)
    return () => {
      offChannelEvent('system', wsHandler)
      unsubscribe(['system'])
    }
  }, [subscribe, unsubscribe, onChannelEvent, offChannelEvent, wsHandler])

  const isActive = status != null && ACTIVE_STAGES.has(status.stage)
  const hasDependencyFailure =
    preflight != null && preflight.checks.some((c) => c.name === 'dependencies' && c.status === 'fail')
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

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader title="Embedding Fine-Tuning" />

      {hasDependencyFailure && <DependencyMissingBanner />}

      {isInitialLoading ? (
        <>
          <SkeletonCard header lines={3} />
          <SkeletonCard header lines={4} />
          <SkeletonCard header lines={5} />
        </>
      ) : (
        <>
          <SectionCard title="Pipeline Control" icon={Settings}>
            <PipelineControlPanel />
          </SectionCard>

          {isActive && (
            <SectionCard title="Progress" icon={Activity}>
              <PipelineStepper stage={status.stage} />
              <PipelineProgressBar
                stage={status.stage}
                progress={status.progress}
              />
            </SectionCard>
          )}

          {showEmptyState ? (
            <SectionCard title="Checkpoints" icon={Database}>
              <EmptyState
                icon={Database}
                title="No fine-tune runs yet"
                description="Kick off a pipeline above to produce your first checkpoint. Completed runs and their checkpoints will show up here."
              />
            </SectionCard>
          ) : (
            <>
              <SectionCard title="Checkpoints" icon={Database}>
                <CheckpointTable />
              </SectionCard>

              <SectionCard title="Run History" icon={Clock}>
                <RunHistoryTable />
              </SectionCard>
            </>
          )}
        </>
      )}
    </div>
  )
}
