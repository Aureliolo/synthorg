import { listProjects } from '@/api/endpoints/projects'
import { getErrorMessage } from '@/utils/errors'
import type { ProjectStatus } from '@/api/types/enums'
import {
  bumpDetailRequestToken,
  isStaleListRequest,
  nextListRequestToken,
} from './_state'
import type { ProjectsSet } from './types'

async function fetchProjectsImpl(set: ProjectsSet): Promise<void> {
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    const result = await listProjects({ limit: 200 })
    if (isStaleListRequest(token)) return
    set({ projects: result.data, totalProjects: result.data.length })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
    // Always clear ``listLoading`` for the latest request -- including
    // the stale-return paths -- so an overlapping fetch can't leave
    // the skeleton stuck on.
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

function clearDetailImpl(set: ProjectsSet): void {
  bumpDetailRequestToken()
  set({
    selectedProject: null,
    projectTasks: [],
    detailLoading: false,
    detailError: null,
  })
}

export function createListActions(set: ProjectsSet) {
  return {
    fetchProjects: () => fetchProjectsImpl(set),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setStatusFilter: (s: ProjectStatus | null) => set({ statusFilter: s }),
    setLeadFilter: (l: string | null) => set({ leadFilter: l }),
    clearDetail: () => clearDetailImpl(set),
  }
}
