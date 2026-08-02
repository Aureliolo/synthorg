/**
 * The questions the organisation is waiting on.
 *
 * Kept out of the conversation store on purpose: ``startNew`` / ``hydrate``
 * reset that store and bump its epoch, which would silently delete a
 * still-open question. A question is rendered *in* the conversation without
 * being *part* of one, so the chat page derives its cards from here.
 *
 * Nothing is persisted client-side; the list is re-hydrated from the backend
 * on every mount.
 */

import { create } from 'zustand'

import {
  answerParkedQuestion,
  declineParkedQuestion,
  listParkedQuestions,
} from '@/api/endpoints/chat-questions'
import type { ParkedQuestion } from '@/api/types/chat-questions'
import type { WsEvent } from '@/api/types/websocket'
import { createLogger } from '@/lib/logger'
import { nextMessageId } from '@/pages/chat/message-id'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { isObject } from '@/utils/type-guards'
import { sanitizeWsString } from '@/utils/ws-sanitize'

const log = createLogger('org-questions')

/** Action types the two human-input tools park a question under. */
const QUESTION_ACTION_TYPES: readonly string[] = [
  'clarify:question',
  'decision:project',
]

/** Bound on the untrusted id / action-type strings read off a WS frame. */
const MAX_WS_ID_LEN = 64

/** One open question plus the stable transcript id its card renders under. */
export interface OrgQuestionRecord {
  question: ParkedQuestion
  /** Minted once per approval id, so a refetch never remounts the card. */
  turnId: number
}

export interface OrgQuestionsState {
  questions: readonly OrgQuestionRecord[]
  loading: boolean
  error: string | null
  resolving: ReadonlySet<string>
  fetchQuestions: () => Promise<void>
  handleWsEvent: (event: WsEvent) => void
  answerQuestion: (
    approvalId: string,
    answer: string,
    chosenOptionId?: string,
  ) => Promise<boolean>
  declineQuestion: (approvalId: string) => Promise<boolean>
  reset: () => void
}

type QuestionsSet = (partial: Partial<OrgQuestionsState>) => void
type QuestionsGet = () => OrgQuestionsState

/**
 * Coalescing flags: a burst of WS events drives one refetch, with no timer.
 * An object rather than two module-level `let`s so the loop below reads the
 * flag the in-flight request may have set, instead of a narrowed constant.
 */
const refetch = { inFlight: false, dirty: false }

/**
 * Read the dirty flag through a call: an inline `refetch.dirty` in the loop
 * below is narrowed to the `false` it was just assigned, so the flag a WS
 * event set mid-request would be compiled away.
 */
function refetchRequested(): boolean {
  return refetch.dirty
}

function initialState(): Pick<
  OrgQuestionsState,
  'questions' | 'loading' | 'error' | 'resolving'
> {
  return { questions: [], loading: false, error: null, resolving: new Set() }
}

function emitCrudError(err: unknown, fallbackTitle: string, logPrefix: string): void {
  log.error(`${logPrefix}:`, getErrorMessage(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

/** Re-key the fresh list against the current one so turn ids stay stable. */
function withStableTurnIds(
  fresh: readonly ParkedQuestion[],
  current: readonly OrgQuestionRecord[],
): OrgQuestionRecord[] {
  const existing = new Map(current.map((r) => [r.question.approval_id, r.turnId]))
  return fresh.map((question) => ({
    question,
    turnId: existing.get(question.approval_id) ?? nextMessageId(),
  }))
}

/** The approval an approval-lifecycle WS event carries, when well-formed. */
function eventApproval(event: WsEvent): Record<string, unknown> | null {
  if (!isObject(event.payload)) return null
  const approval = event.payload['approval']
  return isObject(approval) ? approval : null
}

/** Whether a WS approval event concerns a parked agent question. */
function isQuestionEvent(event: WsEvent): boolean {
  const approval = eventApproval(event)
  if (approval === null) return false
  return QUESTION_ACTION_TYPES.includes(
    sanitizeWsString(approval['action_type'], MAX_WS_ID_LEN) ?? '',
  )
}

/** The approval id a decided-approval WS event refers to. */
function decidedApprovalId(event: WsEvent): string {
  const approval = eventApproval(event)
  if (approval !== null) {
    const fromApproval = sanitizeWsString(approval['id'], MAX_WS_ID_LEN)
    if (fromApproval) return fromApproval
  }
  if (!isObject(event.payload)) return ''
  return sanitizeWsString(event.payload['approval_id'], MAX_WS_ID_LEN) ?? ''
}

async function fetchQuestionsImpl(
  set: QuestionsSet,
  get: QuestionsGet,
): Promise<void> {
  if (refetch.inFlight) {
    refetch.dirty = true
    return
  }
  refetch.inFlight = true
  set({ loading: true })
  try {
    do {
      refetch.dirty = false
      const fresh = await listParkedQuestions()
      set({ questions: withStableTurnIds(fresh, get().questions), error: null })
    } while (refetchRequested())
  } catch (err) {
    // List read: surface the error in state, never a toast.
    log.warn('Load parked questions failed:', getErrorMessage(err))
    set({ error: getErrorMessage(err) })
  } finally {
    refetch.inFlight = false
    set({ loading: false })
  }
}

function handleWsEventImpl(set: QuestionsSet, get: QuestionsGet, event: WsEvent): void {
  if (event.event_type === 'approval.submitted') {
    // Refetch rather than mapping the socket payload: re-deriving the
    // reversibility, the option projection and the ordering here would
    // duplicate the server projection and reintroduce the drift the typed
    // DTO prevents.
    if (isQuestionEvent(event)) void get().fetchQuestions()
    return
  }
  if (
    event.event_type === 'approval.approved' ||
    event.event_type === 'approval.rejected' ||
    event.event_type === 'approval.expired'
  ) {
    const approvalId = decidedApprovalId(event)
    if (!approvalId) return
    set({
      questions: get().questions.filter((r) => r.question.approval_id !== approvalId),
    })
  }
}

interface ResolveSpec {
  approvalId: string
  send: () => Promise<unknown>
  successTitle: string
  errorTitle: string
  logPrefix: string
}

/**
 * Run one answer/decline call, holding the card in a resolving state and
 * dropping it once the backend has recorded the decision.
 */
async function resolveQuestionImpl(
  set: QuestionsSet,
  get: QuestionsGet,
  spec: ResolveSpec,
): Promise<boolean> {
  set({ resolving: new Set(get().resolving).add(spec.approvalId) })
  try {
    await spec.send()
    set({
      questions: get().questions.filter(
        (r) => r.question.approval_id !== spec.approvalId,
      ),
    })
    useToastStore.getState().add({ variant: 'success', title: spec.successTitle })
    return true
  } catch (err) {
    emitCrudError(err, spec.errorTitle, spec.logPrefix)
    return false
  } finally {
    const remaining = new Set(get().resolving)
    remaining.delete(spec.approvalId)
    set({ resolving: remaining })
  }
}

export const useOrgQuestionsStore = create<OrgQuestionsState>()((set, get) => ({
  ...initialState(),

  fetchQuestions: async () => fetchQuestionsImpl(set, get),

  handleWsEvent: (event) => handleWsEventImpl(set, get, event),

  answerQuestion: async (approvalId, answer, chosenOptionId) =>
    resolveQuestionImpl(set, get, {
      approvalId,
      send: async () =>
        answerParkedQuestion(approvalId, {
          answer,
          chosen_option_id: chosenOptionId ?? null,
        }),
      successTitle: 'Answer sent; the agent is resuming',
      errorTitle: 'Failed to send the answer',
      logPrefix: 'Answer question failed',
    }),

  declineQuestion: async (approvalId) =>
    resolveQuestionImpl(set, get, {
      approvalId,
      send: async () => declineParkedQuestion(approvalId),
      successTitle: 'Declined; the agent proceeds on its own judgement',
      errorTitle: 'Failed to decline the question',
      logPrefix: 'Decline question failed',
    }),

  reset: () => {
    refetch.inFlight = false
    refetch.dirty = false
    set(initialState())
  },
}))
