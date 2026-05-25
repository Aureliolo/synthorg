import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import type { WsEvent } from '@/api/types'
import type { ProjectsGet, ProjectsSet } from './types'

const log = createLogger('projects')

function applyProjectDeleted(
  set: ProjectsSet,
  deletedId: string,
): void {
  set((state) => {
    const filtered = state.projects.filter((p) => p.id !== deletedId)
    // If the deleted project is currently open in the detail view,
    // clear it so the user does not keep looking at a row that no
    // longer exists. The route-level guard in ProjectDetailPage will
    // redirect to the list on null selectedProject.
    const clearDetail = state.selectedProject?.id === deletedId
    return {
      projects: filtered,
      selectedProject: clearDetail ? null : state.selectedProject,
      projectTasks: clearDetail ? [] : state.projectTasks,
    }
  })
}

function updateFromWsEventImpl(
  set: ProjectsSet,
  get: ProjectsGet,
  event: WsEvent,
): void {
  // PROJECT_DELETED: drop the row locally before the full refetch
  // lands so the UI reflects the delete immediately. Other event
  // types fall through to a full refetch -- incremental updates are
  // not worth the complexity given 30s polling.
  if (event.event_type === 'project.deleted') {
    const payload = event.payload as { project_id?: unknown }
    // WS payloads are untrusted -- route the identifier through the
    // shared sanitizer so control characters / bidi / oversized
    // strings never land in local state or the UI.
    const deletedId = sanitizeWsString(payload.project_id) ?? null
    if (deletedId) applyProjectDeleted(set, deletedId)
    return
  }
  get().fetchProjects().catch((err: unknown) => {
    log.warn('projects ws refetch failed', sanitizeForLog(err))
  })
}

export function createWsHandler(set: ProjectsSet, get: ProjectsGet) {
  return {
    updateFromWsEvent: (event: WsEvent) =>
      updateFromWsEventImpl(set, get, event),
  }
}
