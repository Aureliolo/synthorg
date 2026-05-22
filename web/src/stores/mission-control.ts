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

  // Flight recorder (read).
  frames: readonly FlightRecorderFrame[]
  framesExecutionId: string | null
  framesLoading: boolean
  framesError: string | null
  seekView: ReplaySeekView | null

  fetchSnapshot: () => Promise<void>
  fetchFrames: (executionId: string) => Promise<void>
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

export const useMissionControlStore = create<MissionControlState>()((set) => ({
  snapshot: null,
  snapshotLoading: false,
  snapshotError: null,
  frames: [],
  framesExecutionId: null,
  framesLoading: false,
  framesError: null,
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
    set({ framesLoading: true, framesError: null, framesExecutionId: executionId })
    try {
      const response = await getFlightRecorderFrames(executionId)
      set({ frames: response.frames, framesLoading: false })
    } catch (err) {
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
