import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  confirmSupersession,
  issueSteering,
  listActiveSteering,
  type IssueSteeringPayload,
} from '@/api/endpoints/steering'
import type {
  ActiveSteeringDirective,
  SteeringIssueResult,
  SteeringSupersessionProposal,
  SteeringSupersessionResult,
} from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('steering')

interface SteeringState {
  // Active directives (read; sets error, never toasts).
  directives: readonly ActiveSteeringDirective[]
  directivesProject: string | null
  directivesLoading: boolean
  directivesError: string | null

  // A PROPOSE-mode obsolete-task set awaiting operator confirm/edit. Set
  // from the issue() response (the issuing operator gets it inline); WS
  // observers only see the directive appear in the list.
  pendingProposal: SteeringSupersessionProposal | null

  fetchDirectives: (projectId: string) => Promise<void>
  issueDirective: (
    payload: IssueSteeringPayload,
  ) => Promise<SteeringIssueResult | null>
  confirmSupersession: (
    directiveId: string,
    projectId: string,
    taskIds: readonly string[],
  ) => Promise<SteeringSupersessionResult | null>
  dismissProposal: () => void
}

type SteerSet = StoreApi<SteeringState>['setState']

async function fetchDirectivesImpl(
  set: SteerSet,
  projectId: string,
): Promise<void> {
  set({
    directivesLoading: true,
    directivesError: null,
    directivesProject: projectId,
  })
  try {
    const directives = await listActiveSteering(projectId)
    // Drop a stale response if the operator switched projects mid-flight.
    if (useSteeringStore.getState().directivesProject !== projectId) return
    set({ directives, directivesLoading: false })
  } catch (err) {
    if (useSteeringStore.getState().directivesProject !== projectId) return
    set({
      directives: [],
      directivesLoading: false,
      directivesError: getErrorMessage(err),
    })
  }
}

async function issueDirectiveImpl(
  set: SteerSet,
  payload: IssueSteeringPayload,
): Promise<SteeringIssueResult | null> {
  try {
    const result = await issueSteering(payload)
    useToastStore.getState().add({
      variant: 'success',
      title: `${payload.kind === 'redirect' ? 'Redirect' : 'Hint'} issued`,
      description: 'In-flight agents adopt it at the next safe turn boundary.',
    })
    set({ pendingProposal: result.proposal })
    await fetchDirectivesImpl(set, payload.project_id)
    return result
  } catch (err) {
    log.error('issue_failed', { error: sanitizeForLog(err) })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to issue directive'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function confirmSupersessionImpl(
  set: SteerSet,
  directiveId: string,
  projectId: string,
  taskIds: readonly string[],
): Promise<SteeringSupersessionResult | null> {
  try {
    const result = await confirmSupersession(directiveId, projectId, taskIds)
    useToastStore.getState().add({
      variant: 'success',
      title: `Superseded ${String(result.cancelled_task_ids.length)} task(s)`,
    })
    set({ pendingProposal: null })
    await fetchDirectivesImpl(set, projectId)
    return result
  } catch (err) {
    log.error('supersede_failed', { error: sanitizeForLog(err) })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to supersede tasks'),
      description: getErrorMessage(err),
    })
    return null
  }
}

export const useSteeringStore = create<SteeringState>()((set) => ({
  directives: [],
  directivesProject: null,
  directivesLoading: false,
  directivesError: null,
  pendingProposal: null,

  fetchDirectives: (projectId) => fetchDirectivesImpl(set, projectId),
  issueDirective: (payload) => issueDirectiveImpl(set, payload),
  confirmSupersession: (directiveId, projectId, taskIds) =>
    confirmSupersessionImpl(set, directiveId, projectId, taskIds),
  dismissProposal: () => set({ pendingProposal: null }),
}))
