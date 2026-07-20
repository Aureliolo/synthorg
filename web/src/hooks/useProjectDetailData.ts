import { useCallback } from 'react'
import { useProjectsStore } from '@/stores/projects'
import { useDetailData } from '@/hooks/useDetailData'
import type { Project, ProjectProgress } from '@/api/types/projects'
import type { Task } from '@/api/types/tasks'
import type { WsChannel } from '@/api/types/websocket'

// `plans` is subscribed alongside `projects` and `tasks` so a plan status
// change (an item completing, a replan) refreshes the initiative view live,
// rather than only on the next poll.
const DETAIL_CHANNELS = [
  'projects',
  'tasks',
  'plans',
] as const satisfies readonly WsChannel[]

export interface UseProjectDetailDataReturn {
  project: Project | null
  projectTasks: readonly Task[]
  projectProgress: ProjectProgress | null
  /** True when the progress fetch failed, distinct from having no plan. */
  projectProgressFailed: boolean
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useProjectDetailData(projectId: string | undefined): UseProjectDetailDataReturn {
  const project = useProjectsStore((s) => s.selectedProject)
  const projectTasks = useProjectsStore((s) => s.projectTasks)
  const projectProgress = useProjectsStore((s) => s.projectProgress)
  const projectProgressFailed = useProjectsStore((s) => s.projectProgressFailed)
  const loading = useProjectsStore((s) => s.detailLoading)
  const error = useProjectsStore((s) => s.detailError)

  const fetchDetail = useCallback(
    (id: string) => useProjectsStore.getState().fetchProjectDetail(id),
    [],
  )
  const clearDetail = useCallback(() => useProjectsStore.getState().clearDetail(), [])

  return useDetailData({
    id: projectId || undefined,
    fetchDetail,
    clearDetail,
    channels: DETAIL_CHANNELS,
    selectors: {
      project,
      projectTasks,
      projectProgress,
      projectProgressFailed,
      loading,
      error,
    },
  })
}
