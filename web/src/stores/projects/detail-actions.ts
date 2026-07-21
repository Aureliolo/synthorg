import { getProject, getProjectProgress } from '@/api/endpoints/projects'
import { listTasks } from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import type { Project, ProjectProgress } from '@/api/types/projects'
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
  progressResult: PromiseSettledResult<ProjectProgress>
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

/** Collect a label per companion fetch that failed, for the partial banner. */
function collectPartialErrors(results: DetailResults): string[] {
  const partial: string[] = []
  if (results.tasksResult.status === 'rejected') {
    partial.push(`tasks: ${getErrorMessage(results.tasksResult.reason)}`)
  }
  if (results.progressResult.status === 'rejected') {
    partial.push(`progress: ${getErrorMessage(results.progressResult.reason)}`)
  }
  return partial
}

function applyDetailResults(
  set: ProjectsSet,
  results: DetailResults,
): void {
  const project = results.projectResult.status === 'fulfilled'
    ? results.projectResult.value
    : null
  if (!project) {
    const reason: unknown = results.projectResult.status === 'rejected'
      ? results.projectResult.reason
      : null
    set({
      detailError: getErrorMessage(reason ?? 'Project not found'),
      selectedProject: null,
    })
    return
  }
  const partialErrors = collectPartialErrors(results)
  set({
    selectedProject: project,
    projectTasks: results.tasksResult.status === 'fulfilled'
      ? results.tasksResult.value.data
      : [],
    projectProgress: results.progressResult.status === 'fulfilled'
      ? results.progressResult.value
      : null,
    // A failed progress fetch is not "this project has no plan yet". Tracked
    // separately so the view can say the progress could not be loaded rather
    // than asserting a domain state it has no evidence for.
    projectProgressFailed: results.progressResult.status === 'rejected',
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
  // Keep the currently displayed project in place while the refetch runs. A
  // WS event on a shared channel can fire for an unrelated plan, and blanking
  // state here would drop the whole page to a skeleton every time one did.
  set({
    detailLoading: true,
    detailError: null,
    projectProgressFailed: false,
  })
  try {
    const [projectResult, tasksResult, progressResult] = await Promise.allSettled([
      getProject(id),
      // Drain every page so the detail view shows the full task list
      // for the project rather than truncating at the first page.
      drainProjectTasks(id).then((data) => ({ data })),
      getProjectProgress(id),
    ])
    if (isStaleDetailRequest(token)) return
    applyDetailResults(set, { projectResult, tasksResult, progressResult })
  } finally {
    if (!isStaleDetailRequest(token)) set({ detailLoading: false })
  }
}

export function createDetailActions(set: ProjectsSet) {
  return {
    fetchProjectDetail: (id: string) => fetchProjectDetailImpl(set, id),
  }
}
