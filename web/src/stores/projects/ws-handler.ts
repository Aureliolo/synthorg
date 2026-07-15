import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsEnumOrNull, sanitizeWsString } from '@/utils/ws-sanitize'
import { AUTONOMY_LEVEL_VALUES } from '@/api/types/enum-values.gen'
import type { AutonomyLevel } from '@/api/types/enums'
import type { WsEvent } from '@/api/types'
import type { ProjectsGet, ProjectsSet } from './types'

const log = createLogger('projects')

function applyAutonomyModeChanged(
  set: ProjectsSet,
  projectId: string,
  newMode: AutonomyLevel | null,
): void {
  set((state) => ({
    projects: state.projects.map((p) =>
      p.id === projectId ? { ...p, autonomy_mode: newMode } : p,
    ),
    selectedProject:
      state.selectedProject?.id === projectId
        ? { ...state.selectedProject, autonomy_mode: newMode }
        : state.selectedProject,
  }))
}

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
  if (event.event_type === 'project.autonomy_mode_changed') {
    const payload = event.payload as { project_id?: unknown; new_mode?: unknown }
    const projectId = sanitizeWsString(payload.project_id) ?? null
    if (!projectId) return
    // ``null`` covers both a cleared override and an unknown value; the
    // periodic refetch reconciles the rare invalid-payload case.
    const newMode = sanitizeWsEnumOrNull<AutonomyLevel>(
      payload.new_mode,
      AUTONOMY_LEVEL_VALUES,
      { field: 'new_mode' },
    )
    applyAutonomyModeChanged(set, projectId, newMode)
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
