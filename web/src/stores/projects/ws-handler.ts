import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsEnumOrNull, sanitizeWsString } from '@/utils/ws-sanitize'
import { AUTONOMY_LEVEL_VALUES } from '@/api/types/enums'
import type { AutonomyLevel } from '@/api/types/enums'
import type { WsEvent } from '@/api/types/websocket'
import type { ProjectsGet, ProjectsSet } from './types'

const log = createLogger('projects')

// WS versions are untrusted: accept only a positive integer.
function sanitizeWsVersion(raw: unknown): number | null {
  return typeof raw === 'number' && Number.isInteger(raw) && raw >= 1 ? raw : null
}

function applyAutonomyModeChanged(
  set: ProjectsSet,
  projectId: string,
  newMode: AutonomyLevel | null,
  newVersion: number,
): void {
  // Monotonic version gate: apply only strictly newer events so a duplicate
  // or out-of-order WS delivery cannot regress local state. Compared per row
  // so the list and the open detail each honour their own version.
  const patch = <T extends { version: number }>(p: T): T =>
    newVersion > p.version
      ? { ...p, autonomy_mode: newMode, version: newVersion }
      : p
  set((state) => ({
    projects: state.projects.map((p) => (p.id === projectId ? patch(p) : p)),
    selectedProject:
      state.selectedProject?.id === projectId
        ? patch(state.selectedProject)
        : state.selectedProject,
  }))
}

function handleAutonomyModeChanged(
  set: ProjectsSet,
  payload: { project_id?: unknown; new_mode?: unknown; new_version?: unknown },
): void {
  const projectId = sanitizeWsString(payload.project_id) ?? null
  if (!projectId) return
  // Apply the mode and version atomically: without a valid version, updating
  // the mode alone would leave the local version stale and 409 the next
  // guarded edit. A missing / malformed version drops the whole event (the
  // periodic refetch reconciles); the backend always sends it.
  const newVersion = sanitizeWsVersion(payload.new_version)
  if (newVersion === null) return
  // A raw ``null`` is a legitimate override clear and applies immediately.
  // A non-null value that fails enum sanitisation is malformed: drop the
  // event rather than wrongly clearing the displayed override.
  if (payload.new_mode === null) {
    applyAutonomyModeChanged(set, projectId, null, newVersion)
    return
  }
  const newMode = sanitizeWsEnumOrNull<AutonomyLevel>(
    payload.new_mode,
    AUTONOMY_LEVEL_VALUES,
    { field: 'new_mode' },
  )
  if (newMode === null) return
  applyAutonomyModeChanged(set, projectId, newMode, newVersion)
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
      projectProgress: clearDetail ? null : state.projectProgress,
      projectProgressFailed: clearDetail ? false : state.projectProgressFailed,
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
    handleAutonomyModeChanged(set, event.payload)
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
