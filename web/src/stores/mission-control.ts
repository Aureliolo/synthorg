import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  getCockpitSnapshot,
  getFlightRecorderFrames,
  getRedTeamReport,
  killTask,
  pauseTask,
  seekFlightRecorder,
} from '@/api/endpoints/cockpit'
import type {
  FlightRecorderFrameResponse,
  LiveActivitySnapshot,
  RedTeamReportRecord,
  ReplaySeekView,
} from '@/api/types/cockpit'
import type { Task } from '@/api/types/tasks'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('mission-control')

interface MissionControlState {
  // Live activity (read; sets error, never toasts).
  snapshot: LiveActivitySnapshot | null
  snapshotLoading: boolean
  snapshotError: string | null

  // Flight recorder (read; cursor-paginated).
  frames: readonly FlightRecorderFrameResponse[]
  framesExecutionId: string | null
  framesLoading: boolean
  framesError: string | null
  framesNextCursor: string | null
  framesHasMore: boolean
  seekView: ReplaySeekView | null

  // Durable red-team verdict for the loaded run (read; null when no gate
  // ran for the execution or the archive is unwired).
  redTeamReport: RedTeamReportRecord | null
  // Set when the verdict fetch itself failed, so the UI can distinguish a
  // genuine "no verdict recorded" (report null, error null) from a failed
  // read (report null, error set) rather than collapsing both to "none".
  redTeamReportError: string | null

  fetchSnapshot: () => Promise<void>
  fetchFrames: (executionId: string) => Promise<void>
  fetchMoreFrames: () => Promise<void>
  seek: (executionId: string, turnIndex: number) => Promise<void>

  // Interventions (mutation pattern: toast + sentinel; callers do NOT wrap).
  // Mid-flight hint/redirect is project-scoped now -- see useSteeringStore.
  pauseTaskAction: (taskId: string, reason: string) => Promise<Task | null>
  killTaskAction: (taskId: string, reason: string) => Promise<Task | null>
}

type McSet = StoreApi<MissionControlState>['setState']
type McGet = StoreApi<MissionControlState>['getState']

async function fetchSnapshotImpl(set: McSet): Promise<void> {
  set({ snapshotLoading: true, snapshotError: null })
  try {
    const snapshot = await getCockpitSnapshot()
    set({ snapshot, snapshotLoading: false })
  } catch (err) {
    set({ snapshotLoading: false, snapshotError: getErrorMessage(err) })
  }
}

async function fetchFramesImpl(
  set: McSet,
  get: McGet,
  executionId: string,
): Promise<void> {
  set({
    frames: [],
    seekView: null,
    redTeamReport: null,
    redTeamReportError: null,
    framesLoading: true,
    framesError: null,
    framesExecutionId: executionId,
    framesNextCursor: null,
    framesHasMore: false,
  })
  const requestExecutionId = executionId
  try {
    const page = await getFlightRecorderFrames(executionId)
    if (get().framesExecutionId !== requestExecutionId) return
    set({
      frames: page.data,
      framesLoading: false,
      framesNextCursor: page.nextCursor,
      framesHasMore: page.hasMore,
    })
  } catch (err) {
    if (get().framesExecutionId !== requestExecutionId) return
    set({
      frames: [],
      seekView: null,
      framesLoading: false,
      framesError: getErrorMessage(err),
      framesNextCursor: null,
      framesHasMore: false,
    })
    return
  }
  await fetchRedTeamReportImpl(set, get, requestExecutionId)
}

async function fetchRedTeamReportImpl(
  set: McSet,
  get: McGet,
  requestExecutionId: string,
): Promise<void> {
  // Best-effort audit-trail read: a missing verdict is a normal "no
  // red-team review recorded" state, and an error must not mask the frames
  // the operator came to see, so a failure leaves the frames intact and
  // surfaces redTeamReportError instead of masquerading as "no verdict".
  try {
    const report = await getRedTeamReport(requestExecutionId)
    if (get().framesExecutionId !== requestExecutionId) return
    set({ redTeamReport: report, redTeamReportError: null })
  } catch (err) {
    if (get().framesExecutionId !== requestExecutionId) return
    log.warn('red_team_report_fetch_failed', { error: sanitizeForLog(err) })
    set({ redTeamReport: null, redTeamReportError: getErrorMessage(err) })
  }
}

async function fetchMoreFramesImpl(
  set: McSet,
  get: McGet,
): Promise<void> {
  const state = get()
  if (!state.framesHasMore || state.framesNextCursor === null) return
  if (state.framesExecutionId === null) return
  // Gate on the in-flight flag so concurrent fetchMoreFrames() calls
  // cannot read the same framesNextCursor before framesLoading is
  // set and append duplicate frame pages.
  if (state.framesLoading) return
  const cursor = state.framesNextCursor
  const executionId = state.framesExecutionId
  const requestExecutionId = executionId
  set({ framesLoading: true, framesError: null })
  try {
    const page = await getFlightRecorderFrames(executionId, { cursor })
    if (get().framesExecutionId !== requestExecutionId) return
    set({
      frames: [...get().frames, ...page.data],
      framesLoading: false,
      framesNextCursor: page.nextCursor,
      framesHasMore: page.hasMore,
    })
  } catch (err) {
    if (get().framesExecutionId !== requestExecutionId) return
    set({ framesLoading: false, framesError: getErrorMessage(err) })
  }
}

/**
 * Name what a steering action acted on, never its id.
 *
 * The call returns the task it just moved, so the title is in hand by the
 * time the toast is written. A task with no title falls back to the generic
 * wording rather than to the key: an id identifies the row to the database
 * and nothing at all to the operator reading a confirmation.
 */
function steeringSuccessTitle(verb: string, task: { title?: string | null }): string {
  const title = task.title
  return title === undefined || title === null || title === ''
    ? `${verb} the task`
    : `${verb} ${title}`
}

async function steeringAction<T extends { title?: string | null }>(
  call: () => Promise<T>,
  verb: string,
  errorTitle: string,
  logKey: string,
): Promise<T | null> {
  try {
    const outcome = await call()
    useToastStore
      .getState()
      .add({ variant: 'success', title: steeringSuccessTitle(verb, outcome) })
    return outcome
  } catch (err) {
    log.error(logKey, { error: sanitizeForLog(err) })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, errorTitle),
      description: getErrorMessage(err),
    })
    return null
  }
}

export const useMissionControlStore = create<MissionControlState>()((set, get) => ({
  snapshot: null,
  snapshotLoading: false,
  snapshotError: null,
  frames: [],
  framesExecutionId: null,
  framesLoading: false,
  framesError: null,
  framesNextCursor: null,
  framesHasMore: false,
  seekView: null,
  redTeamReport: null,
  redTeamReportError: null,

  fetchSnapshot: () => fetchSnapshotImpl(set),
  fetchFrames: (executionId) => fetchFramesImpl(set, get, executionId),
  fetchMoreFrames: () => fetchMoreFramesImpl(set, get),

  seek: async (executionId, turnIndex) => {
    try {
      const seekView = await seekFlightRecorder(executionId, turnIndex)
      set({ seekView })
    } catch (err) {
      set({ seekView: null, framesError: getErrorMessage(err) })
    }
  },

  pauseTaskAction: (taskId, reason) =>
    steeringAction(
      async () => {
        const task = await pauseTask(taskId, reason)
        return task
      },
      'Paused',
      'Failed to pause task',
      'pause_failed',
    ),
  killTaskAction: (taskId, reason) =>
    steeringAction(
      async () => {
        const task = await killTask(taskId, reason)
        return task
      },
      'Killed',
      'Failed to kill task',
      'kill_failed',
    ),
}))
