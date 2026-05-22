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

  fetchSnapshot: async () => {
    set({ snapshotLoading: true, snapshotError: null })
    try {
      const snapshot = await getCockpitSnapshot()
      set({ snapshot, snapshotLoading: false })
    } catch (err) {
      set({ snapshotLoading: false, snapshotError: getErrorMessage(err) })
    }
  },

  fetchFrames: async (executionId: string) => {
    // Clear the previous run's frames + seekView synchronously so a
    // failed fetch cannot leave the UI showing a different execution's
    // timeline alongside the new ``framesExecutionId``.
    set({
      frames: [],
      seekView: null,
      framesLoading: true,
      framesError: null,
      framesExecutionId: executionId,
      framesNextCursor: null,
      framesHasMore: false,
    })
    // Capture the executionId we started with; the async page-fetch can
    // race against a subsequent ``fetchFrames(other-execution)`` call
    // and we must not apply this page's data once the store has moved
    // on to a different execution.
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
  },

  fetchMoreFrames: async () => {
    const state = get()
    if (!state.framesHasMore || state.framesNextCursor === null) return
    if (state.framesExecutionId === null) return
    const cursor = state.framesNextCursor
    const executionId = state.framesExecutionId
    // Same race guard as ``fetchFrames``: by the time the page arrives,
    // the user may have loaded a different execution and we must not
    // append this page's frames onto an unrelated timeline.
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
  },

  seek: async (executionId: string, turnIndex: number) => {
    try {
      const seekView = await seekFlightRecorder(executionId, turnIndex)
      set({ seekView })
    } catch (err) {
      set({ seekView: null, framesError: getErrorMessage(err) })
    }
  },

  pauseTaskAction: async (taskId: string, reason: string) => {
    try {
      const task = await pauseTask(taskId, reason)
      useToastStore.getState().add({ variant: 'success', title: `Paused task ${task.id}` })
      return task
    } catch (err) {
      log.error('pause_failed', { error: sanitizeForLog(err) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to pause task'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  killTaskAction: async (taskId: string, reason: string) => {
    try {
      const task = await killTask(taskId, reason)
      useToastStore.getState().add({ variant: 'success', title: `Killed task ${task.id}` })
      return task
    } catch (err) {
      log.error('kill_failed', { error: sanitizeForLog(err) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to kill task'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  sendHintAction: async (executionId: string, agentId: string, text: string) => {
    try {
      const outcome = await sendHint(executionId, agentId, text)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Hint queued for the next safe turn boundary',
      })
      return outcome
    } catch (err) {
      log.error('hint_failed', { error: sanitizeForLog(err) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to send hint'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  redirectAction: async (executionId: string, agentId: string, text: string) => {
    try {
      const outcome = await redirectAgent(executionId, agentId, text)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Redirect queued for the next safe turn boundary',
      })
      return outcome
    } catch (err) {
      log.error('redirect_failed', { error: sanitizeForLog(err) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to redirect agent'),
        description: getErrorMessage(err),
      })
      return null
    }
  },
}))
