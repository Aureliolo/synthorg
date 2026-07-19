import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import { addPlanComment, listPlanComments } from '@/api/endpoints/plan-comments'
import type { PlanItemComment } from '@/api/types/plans'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-comments')

// Monotonic request token: a plan-navigation change can leave an older thread
// fetch in flight, and its late resolve must not clobber the current thread.
let requestToken = 0

export interface PlanCommentsState {
  comments: PlanItemComment[]
  loading: boolean
  error: string | null
  fetchComments: (planId: string) => Promise<void>
  addComment: (
    planId: string,
    itemId: string,
    body: string,
    replyToId?: string,
  ) => Promise<PlanItemComment | null>
  reset: () => void
}

type PcSet = StoreApi<PlanCommentsState>['setState']
type PcGet = StoreApi<PlanCommentsState>['getState']

async function fetchCommentsImpl(set: PcSet, planId: string): Promise<void> {
  const token = (requestToken += 1)
  set({ loading: true, error: null, comments: [] })
  try {
    const comments = await listPlanComments(planId)
    if (token !== requestToken) return
    set({ comments, loading: false })
  } catch (err) {
    if (token !== requestToken) return
    const message = getErrorMessage(err)
    log.warn('Fetch plan comments failed', sanitizeForLog(err))
    set({ loading: false, error: message })
  }
}

// Reconcile under the caller's captured generation (does NOT bump the token):
// a refresh for a plan the operator has since navigated away from must not
// become the newest token and overwrite the current plan's thread.
async function refreshCommentsImpl(
  set: PcSet,
  planId: string,
  token: number,
): Promise<void> {
  try {
    const comments = await listPlanComments(planId)
    // Drop a stale reload once the operator has navigated to another plan.
    if (token !== requestToken) return
    set({ comments })
  } catch (err) {
    if (token !== requestToken) return
    // Keep whatever is on screen (the operator's own comment); no error toast
    // for a background reconcile.
    log.warn('Refresh plan comments failed', sanitizeForLog(err))
  }
}

interface AddCommentArgs {
  planId: string
  itemId: string
  body: string
  replyToId?: string
}

async function addCommentImpl(
  set: PcSet,
  get: PcGet,
  args: AddCommentArgs,
): Promise<PlanItemComment | null> {
  const { planId, itemId, body, replyToId } = args
  // The active-plan generation when this post began: a navigation to another
  // plan (fetch / reset) bumps it, and both the append and the reconcile below
  // are dropped so a post for plan A cannot land in (or refresh over) plan B.
  const token = requestToken
  try {
    const comment = await addPlanComment(planId, itemId, {
      body,
      ...(replyToId != null && { reply_to_id: replyToId }),
    })
    if (token !== requestToken) return comment
    // Append if not already present (a WS echo of our own post may race).
    if (!get().comments.some((c) => c.id === comment.id)) {
      set({ comments: [...get().comments, comment] })
    }
    // The responsible role may answer inline: that reply is persisted within
    // the POST (server-side, before it returns), so a re-list surfaces it and
    // reconciles against the backend truth (the pure-API-consumer contract).
    // Silent -- no loading flash -- and best-effort: a failed refresh leaves
    // the operator's own optimistic comment in place.
    await refreshCommentsImpl(set, planId, token)
    useToastStore.getState().add({ variant: 'success', title: 'Comment added' })
    return comment
  } catch (err) {
    log.error('Add plan comment failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to post comment'),
      description: getErrorMessage(err),
    })
    return null
  }
}

/**
 * Per-item plan comment threads for the plan currently open in the workspace. A
 * pure API consumer: the thread is re-hydrated from the backend on mount and
 * every post writes through the API; nothing is persisted client-side.
 */
export const usePlanCommentsStore = create<PlanCommentsState>((set, get) => ({
  comments: [],
  loading: false,
  error: null,
  fetchComments: (planId) => fetchCommentsImpl(set, planId),
  addComment: (planId, itemId, body, replyToId) =>
    addCommentImpl(set, get, {
      planId,
      itemId,
      body,
      ...(replyToId != null && { replyToId }),
    }),
  reset: () => {
    requestToken += 1
    set({ comments: [], loading: false, error: null })
  },
}))
