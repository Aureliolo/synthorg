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
import type {
  ParkedQuestion,
  QuestionDecisionResult,
} from '@/api/types/chat-questions'
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
  /** True when the backend has more open questions than this page holds. */
  hasMore: boolean
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
 * Coalescing + staleness state for the list read.
 *
 * ``epoch`` advances on every local removal (an answered card, a decided WS
 * frame). A fetch captures it when it is ISSUED and discards its own response
 * if it changed meanwhile, because that response was read before the removal
 * and would resurrect the card the operator just cleared.
 */
const refetch = { inFlight: false, dirty: false, epoch: 0 }

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
  'questions' | 'hasMore' | 'loading' | 'error' | 'resolving'
> {
  return {
    questions: [],
    hasMore: false,
    loading: false,
    error: null,
    resolving: new Set(),
  }
}

function emitCrudError(err: unknown, fallbackTitle: string, logPrefix: string): void {
  log.error(`${logPrefix}:`, getErrorMessage(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

/** Whether two option lists render identically, in the same order. */
function sameOptions(
  a: ParkedQuestion['options'],
  b: ParkedQuestion['options'],
): boolean {
  return (
    a.length === b.length &&
    a.every((opt, i) => {
      const prev = b[i]
      return (
        prev !== undefined &&
        opt.id === prev.id &&
        opt.title === prev.title &&
        opt.summary === prev.summary &&
        opt.recommended === prev.recommended
      )
    })
  )
}

/** Whether two questions render identically, so the old object can be reused. */
function sameQuestion(a: ParkedQuestion, b: ParkedQuestion): boolean {
  return (
    a.question === b.question &&
    a.asked_by_name === b.asked_by_name &&
    a.task_title === b.task_title &&
    a.project === b.project &&
    a.reversibility === b.reversibility &&
    a.is_decision === b.is_decision &&
    a.asked_at === b.asked_at &&
    sameOptions(a.options, b.options)
  )
}

/**
 * Re-key the fresh list against the current one so turn ids stay stable.
 *
 * An unchanged question keeps its PREVIOUS record object, not just its turn
 * id: the transcript memoises each turn on identity, so allocating a new
 * object every poll tick would re-render every open card twice a minute.
 */
function withStableTurnIds(
  fresh: readonly ParkedQuestion[],
  current: readonly OrgQuestionRecord[],
): OrgQuestionRecord[] {
  const existing = new Map(current.map((r) => [r.question.approval_id, r]))
  return fresh.map((question) => {
    const prev = existing.get(question.approval_id)
    if (prev !== undefined && sameQuestion(prev.question, question)) return prev
    return { question, turnId: prev?.turnId ?? nextMessageId() }
  })
}

/** The approval an approval-lifecycle WS event carries, when well-formed. */
function eventApproval(event: WsEvent): Record<string, unknown> | null {
  if (!isObject(event.payload)) return null
  const approval = event.payload['approval']
  return isObject(approval) ? approval : null
}

/**
 * Whether a WS approval event concerns a parked agent question.
 *
 * Exported so the polling gate can be scoped to the same predicate: marking
 * the list fresh on unrelated approvals traffic would let a busy queue
 * suppress the poll that is the fallback for a dropped socket.
 */
export function isQuestionEvent(event: WsEvent): boolean {
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

/** Drop one question locally and invalidate any read issued before now. */
function removeQuestion(
  set: QuestionsSet,
  get: QuestionsGet,
  approvalId: string,
): void {
  refetch.epoch += 1
  set({
    questions: get().questions.filter((r) => r.question.approval_id !== approvalId),
  })
}

/**
 * Ceiling on passes within one ``fetchQuestions`` call.
 *
 * Each pass is driven by real state change (a WS event arriving mid-request,
 * or a card removed while the read was in flight), so the loop terminates on
 * its own. The cap is a backstop against a pathological event storm turning
 * one poll tick into an unbounded request loop, not the normal exit.
 */
const MAX_FETCH_PASSES = 5

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
    for (let pass = 0; pass < MAX_FETCH_PASSES; pass++) {
      refetch.dirty = false
      const issuedAt = refetch.epoch
      try {
        const page = await listParkedQuestions()
        if (issuedAt !== refetch.epoch) {
          // A card was answered or decided while this read was in flight, so
          // the response predates the removal and would put it back. Re-run
          // rather than apply it.
          continue
        }
        set({
          questions: withStableTurnIds(page.data, get().questions),
          hasMore: page.hasMore,
          error: null,
        })
      } catch (err) {
        // List read: surface the error in state, never a toast. Stay in the
        // loop so a refetch requested during the failed attempt is still
        // honoured rather than waiting out a poll interval.
        log.warn('Load parked questions failed:', getErrorMessage(err))
        set({ error: getErrorMessage(err) })
      }
      if (!refetchRequested()) break
    }
  } finally {
    refetch.inFlight = false
    refetch.dirty = false
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
    removeQuestion(set, get, approvalId)
  }
}

interface ResolveSpec {
  approvalId: string
  send: (idempotencyKey: string) => Promise<QuestionDecisionResult>
  successTitle: string
  errorTitle: string
  logPrefix: string
  /**
   * What to add to the success toast, given what the server actually
   * recorded. Returning undefined leaves the toast bare.
   */
  describe?: (result: QuestionDecisionResult) => string | undefined
}

/**
 * One idempotency key per open question per action, minted on first use.
 *
 * A retry after a timeout has to carry the SAME key or the server cannot
 * recognise it as a replay: it would re-decide, hit the already-decided
 * conflict, and tell the operator their answer failed when it landed. Keys
 * are dropped once the question is resolved.
 */
const idempotencyKeys = new Map<string, string>()

function idempotencyKeyFor(scope: string): string {
  const existing = idempotencyKeys.get(scope)
  if (existing !== undefined) return existing
  const minted = crypto.randomUUID()
  idempotencyKeys.set(scope, minted)
  return minted
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
  const scope = `${spec.logPrefix}:${spec.approvalId}`
  try {
    const result = await spec.send(idempotencyKeyFor(scope))
    idempotencyKeys.delete(scope)
    removeQuestion(set, get, spec.approvalId)
    const description = spec.describe?.(result)
    useToastStore.getState().add({
      variant: 'success',
      title: spec.successTitle,
      ...(description === undefined ? {} : { description }),
    })
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
      send: async (key) =>
        answerParkedQuestion(
          approvalId,
          { answer, chosen_option_id: chosenOptionId ?? null },
          key,
        ),
      successTitle: 'Answer sent; the agent is resuming',
      errorTitle: 'Failed to send the answer',
      logPrefix: 'Answer question failed',
      // On a decision the operator picks an option and the SERVER decides the
      // wording the agent gets (the option's writeup, not the button label),
      // so echoing the recorded text is the only way they see what was sent.
      describe: (result) =>
        result.recorded_answer === answer
          ? undefined
          : `The agent received: ${result.recorded_answer}`,
    }),

  declineQuestion: async (approvalId) =>
    resolveQuestionImpl(set, get, {
      approvalId,
      send: async (key) => declineParkedQuestion(approvalId, key),
      successTitle: 'Declined; the agent proceeds on its own judgement',
      errorTitle: 'Failed to decline the question',
      logPrefix: 'Decline question failed',
    }),

  reset: () => {
    // Advancing the epoch invalidates any read still in flight, so an orphan
    // response cannot land on the state this reset just cleared.
    refetch.epoch += 1
    refetch.inFlight = false
    refetch.dirty = false
    idempotencyKeys.clear()
    set(initialState())
  },
}))
