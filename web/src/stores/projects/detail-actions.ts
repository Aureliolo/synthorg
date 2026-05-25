import { getProject } from '@/api/endpoints/projects'
import { listTasks } from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import type { Project } from '@/api/types/projects'
import type { Task } from '@/api/types/tasks'
import {
  isStaleDetailRequest,
  nextDetailRequestToken,
} from './_state'
import type { ProjectsSet } from './types'

const TASKS_PAGE_LIMIT = 50

// Safety stop so a backend bug that keeps returning ``has_more=true``
// cannot lock the dashboard in an infinite drain loop.
const TASKS_DRAIN_PAGE_LIMIT = 50

interface DetailResults {
  projectResult: PromiseSettledResult<Project>
  tasksResult: PromiseSettledResult<{ data: Task[] }>
}

async function drainProjectTasks(projectId: string): Promise<Task[]> {
  const collected: Task[] = []
  let cursor: string | null = null
  for (let i = 0; i < TASKS_DRAIN_PAGE_LIMIT; i++) {
    const page = await listTasks({
      project: projectId,
      limit: TASKS_PAGE_LIMIT,
      cursor,
    })
    collected.push(...page.data)
    if (!page.hasMore || !page.nextCursor) return collected
    cursor = page.nextCursor
  }
  return collected
}

function applyDetailResults(
  set: ProjectsSet,
  results: DetailResults,
): void {
  const project = results.projectResult.status === 'fulfilled'
    ? results.projectResult.value
    : null
  if (!project) {
    const reason = results.projectResult.status === 'rejected'
      ? results.projectResult.reason
      : null
    set({
      detailError: getErrorMessage(reason ?? 'Project not found'),
      selectedProject: null,
    })
    return
  }
  const partialErrors: string[] = []
  if (results.tasksResult.status === 'rejected') {
    partialErrors.push(`tasks: ${getErrorMessage(results.tasksResult.reason)}`)
  }
  set({
    selectedProject: project,
    projectTasks: results.tasksResult.status === 'fulfilled'
      ? results.tasksResult.value.data
      : [],
    detailError: partialErrors.length > 0
      ? `Some data failed to load: ${partialErrors.join(', ')}. Displayed data may be incomplete.`
      : null,
  })
}

async function fetchProjectDetailImpl(
  set: ProjectsSet,
  id: string,
): Promise<void> {
  const token = nextDetailRequestToken()
  set({
    detailLoading: true,
    detailError: null,
    selectedProject: null,
    projectTasks: [],
  })
  try {
    const [projectResult, tasksResult] = await Promise.allSettled([
      getProject(id),
      // Drain every page so the detail view shows the full task list
      // for the project rather than truncating at the first page.
      drainProjectTasks(id).then((data) => ({ data })),
    ])
    if (isStaleDetailRequest(token)) return
    applyDetailResults(set, { projectResult, tasksResult })
  } finally {
    if (!isStaleDetailRequest(token)) set({ detailLoading: false })
  }
}

export function createDetailActions(set: ProjectsSet) {
  return {
    fetchProjectDetail: (id: string) => fetchProjectDetailImpl(set, id),
  }
}
