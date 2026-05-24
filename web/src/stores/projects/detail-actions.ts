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

interface DetailResults {
  projectResult: PromiseSettledResult<Project>
  tasksResult: PromiseSettledResult<{ data: Task[] }>
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
      listTasks({ project: id, limit: 50 }),
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
