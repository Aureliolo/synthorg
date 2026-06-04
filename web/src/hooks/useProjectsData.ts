import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useProjectsStore } from '@/stores/projects'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type { Project } from '@/api/types/projects'
import type { WsChannel } from '@/api/types/websocket'

const PROJECTS_POLL_INTERVAL = 30_000
const WS_DEBOUNCE_MS = 300
const PROJECT_CHANNELS = ['projects'] as const satisfies readonly WsChannel[]

export interface UseProjectsDataReturn {
  projects: readonly Project[]
  filteredProjects: readonly Project[]
  totalProjects: number
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useProjectsData(): UseProjectsDataReturn {
  const projects = useProjectsStore((s) => s.projects)
  const totalProjects = projects.length
  const loading = useProjectsStore((s) => s.listLoading)
  const error = useProjectsStore((s) => s.listError)
  const searchQuery = useProjectsStore((s) => s.searchQuery)
  const statusFilter = useProjectsStore((s) => s.statusFilter)
  const leadFilter = useProjectsStore((s) => s.leadFilter)

  useEffect(() => {
    void useProjectsStore.getState().fetchProjects()
  }, [])

  const pollFn = useCallback(async () => {
    await useProjectsStore.getState().fetchProjects()
  }, [])
  const polling = usePolling(pollFn, PROJECTS_POLL_INTERVAL)

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
      PROJECT_CHANNELS.map((channel) => ({
        channel,
        handler: () => {
          if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
          wsDebounceRef.current = setTimeout(() => {
            void useProjectsStore.getState().fetchProjects()
          }, WS_DEBOUNCE_MS)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  const filteredProjects = useMemo(() => {
    let result = [...projects]
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q),
      )
    }
    if (statusFilter) result = result.filter((p) => p.status === statusFilter)
    if (leadFilter) result = result.filter((p) => p.lead === leadFilter)
    return result
  }, [projects, searchQuery, statusFilter, leadFilter])

  return { projects, filteredProjects, totalProjects, loading, error, wsConnected, wsSetupError }
}
