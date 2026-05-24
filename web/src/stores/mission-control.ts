import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  getCockpitSnapshot,
  getFlightRecorderFrames,
  killTask,
  pauseTask,
  redirectAgent,
  seekFlightRecorder,
  sendHint,
} from '@/api/endpoints/cockpit'
import type {
  FlightRecorderFrame,
  LiveActivitySnapshot,
  ReplaySeekView,
  SteeringOutcome,
  Task,
} from '@/api/types'
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
  frames: readonly FlightRecorderFrame[]
  framesExecutionId: string | null
  framesLoading: boolean
  framesError: string | null
  framesNextCursor: string | null
  framesHasMore: boolean
  seekView: ReplaySeekView | null

  fetchSnapshot: () => Promise<void>
  fetchFrames: (executionId: string) => Promise<void>
  fetchMoreFrames: () => Promise<void>
  seek: (executionId: string, turnIndex: number) => Promise<void>

  // Interventions (mutation pattern: toast + sentinel; callers do NOT wrap).
  pauseTaskAction: (taskId: string, reason: string) => Promise<Task | null>
  killTaskAction: (taskId: string, reason: string) => Promise<Task | null>
  sendHintAction: (
    executionId: string,
    agentId: string,
    text: string,
  ) => Promise<SteeringOutcome | null>
  redirectAction: (
    executionId: string,
    agentId: string,
    text: string,
  ) => Promise<SteeringOutcome | null>
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
  }
}

async function fetchMoreFramesImpl(
  set: McSet,
  get: McGet,
): Promise<void> {
  const state = get()
  if (!state.framesHasMore || state.framesNextCursor === null) return
  if (state.framesExecutionId === null) return
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

async function steeringAction<T>(
  call: () => Promise<T>,
  successTitle: string,
  errorTitle: string,
  logKey: string,
): Promise<T | null> {
  try {
    const outcome = await call()
    useToastStore.getState().add({ variant: 'success', title: successTitle })
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
      `Paused task ${taskId}`,
      'Failed to pause task',
      'pause_failed',
    ),
  killTaskAction: (taskId, reason) =>
    steeringAction(
      async () => {
        const task = await killTask(taskId, reason)
        return task
      },
      `Killed task ${taskId}`,
      'Failed to kill task',
      'kill_failed',
    ),
  sendHintAction: (executionId, agentId, text) =>
    steeringAction(
      () => sendHint(executionId, agentId, text),
      'Hint queued for the next safe turn boundary',
      'Failed to send hint',
      'hint_failed',
    ),
  redirectAction: (executionId, agentId, text) =>
    steeringAction(
      () => redirectAgent(executionId, agentId, text),
      'Redirect queued for the next safe turn boundary',
      'Failed to redirect agent',
      'redirect_failed',
    ),
}))
