import { listProjects } from '@/api/endpoints/projects'
import { getErrorMessage } from '@/utils/errors'
import type { ProjectStatus } from '@/api/types/enums'
import {
  bumpDetailRequestToken,
  isStaleListRequest,
  nextListRequestToken,
} from './_state'
import type { ProjectsGet, ProjectsSet } from './types'

const PROJECTS_PAGE_LIMIT = 200

async function fetchProjectsImpl(set: ProjectsSet): Promise<void> {
  const token = nextListRequestToken()
  set({
    listLoading: true,
    listError: null,
    nextCursor: null,
    hasMore: false,
  })
  try {
    const result = await listProjects({ limit: PROJECTS_PAGE_LIMIT })
    if (isStaleListRequest(token)) return
    set({
      projects: result.data,
      nextCursor: result.nextCursor,
      hasMore: result.hasMore,
    })
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

async function fetchMoreProjectsImpl(
  set: ProjectsSet,
  get: ProjectsGet,
): Promise<void> {
  const { listLoading, hasMore, nextCursor } = get()
  if (listLoading || !hasMore || !nextCursor) return
  const token = nextListRequestToken()
  set({ listLoading: true, listError: null })
  try {
    const result = await listProjects({
      cursor: nextCursor,
      limit: PROJECTS_PAGE_LIMIT,
    })
    if (isStaleListRequest(token)) return
    set((s) => {
      const existingIds = new Set(s.projects.map((p) => p.id))
      const deduped = result.data.filter((p) => !existingIds.has(p.id))
      return {
        projects: [...s.projects, ...deduped],
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      }
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    set({ listError: getErrorMessage(err) })
  } finally {
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

export function createListActions(set: ProjectsSet, get: ProjectsGet) {
  return {
    fetchProjects: () => fetchProjectsImpl(set),
    fetchMoreProjects: () => fetchMoreProjectsImpl(set, get),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setStatusFilter: (s: ProjectStatus | null) => set({ statusFilter: s }),
    setLeadFilter: (l: string | null) => set({ leadFilter: l }),
    clearDetail: () => clearDetailImpl(set),
  }
}
