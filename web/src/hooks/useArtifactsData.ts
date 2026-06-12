import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useArtifactsStore } from '@/stores/artifacts'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import type { Artifact } from '@/api/types/artifacts'
import type { WsChannel } from '@/api/types/websocket'

const ARTIFACTS_POLL_INTERVAL = 30_000
const WS_DEBOUNCE_MS = 300
const ARTIFACT_CHANNELS = ['artifacts'] as const satisfies readonly WsChannel[]

export interface UseArtifactsDataReturn {
  artifacts: readonly Artifact[]
  filteredArtifacts: readonly Artifact[]
  totalArtifacts: number
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useArtifactsData(): UseArtifactsDataReturn {
  const artifacts = useArtifactsStore((s) => s.artifacts)
  const totalArtifacts = artifacts.length
  const loading = useArtifactsStore((s) => s.listLoading)
  const error = useArtifactsStore((s) => s.listError)
  const searchQuery = useArtifactsStore((s) => s.searchQuery)
  const typeFilter = useArtifactsStore((s) => s.typeFilter)
  const createdByFilter = useArtifactsStore((s) => s.createdByFilter)
  const taskIdFilter = useArtifactsStore((s) => s.taskIdFilter)
  const contentTypeFilter = useArtifactsStore((s) => s.contentTypeFilter)
  const projectIdFilter = useArtifactsStore((s) => s.projectIdFilter)

  useEffect(() => {
    void useArtifactsStore.getState().fetchArtifacts()
  }, [])

  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useArtifactsStore.getState().fetchArtifacts()
  }, [])
  const polling = usePolling(pollFn, ARTIFACTS_POLL_INTERVAL, { skipIfFresh })

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
  }, [])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      ARTIFACT_CHANNELS.map((channel) => ({
        channel,
        handler: () => {
          markFresh()
          if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
          wsDebounceRef.current = setTimeout(() => {
            void useArtifactsStore.getState().fetchArtifacts()
          }, WS_DEBOUNCE_MS)
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  const filteredArtifacts = useMemo(() => {
    let result = [...artifacts]
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      result = result.filter(
        (a) =>
          a.path.toLowerCase().includes(q) ||
          a.description.toLowerCase().includes(q) ||
          a.id.toLowerCase().includes(q),
      )
    }
    if (typeFilter) result = result.filter((a) => a.type === typeFilter)
    if (createdByFilter) result = result.filter((a) => a.created_by === createdByFilter)
    if (taskIdFilter) result = result.filter((a) => a.task_id === taskIdFilter)
    if (contentTypeFilter) result = result.filter((a) => a.content_type.startsWith(contentTypeFilter))
    if (projectIdFilter) result = result.filter((a) => a.project_id === projectIdFilter)
    return result
  }, [artifacts, searchQuery, typeFilter, createdByFilter, taskIdFilter, contentTypeFilter, projectIdFilter])

  return { artifacts, filteredArtifacts, totalArtifacts, loading, error, wsConnected, wsSetupError }
}
