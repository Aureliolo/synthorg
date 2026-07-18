import { create } from 'zustand'

import { parseCitedRecords } from '@/api/endpoints/cited-records'
import {
  postTurn,
  streamTurn,
  type PostTurnOptions,
  type StreamTurnOutcome,
} from '@/api/endpoints/meta'
import type { TurnIntent, TurnResult } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useApprovalsStore } from '@/stores/approvals'
import { useCharterStore } from '@/stores/charter'
import { useToastStore } from '@/stores/toast'
import { describeConversationalError } from '@/utils/conversational-error'
import { isAbortError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

import { nextMessageId } from '@/pages/chat/message-id'
import type { OrgAssistantTurn, OrgTurn } from '@/pages/chat/org-chat-types'
import { intentDegradeNotice, mapTurnResult } from '@/pages/chat/org-turn-map'

const log = createLogger('org-conversation')

/**
 * In-memory transcript store for the one unified org conversation.
 *
 * A single thread that talks to the whole org: the operator sends a message,
 * the server classifies and dispatches it, and the resolved turns render here.
 * Plain ``create`` with NO ``persist`` and NO client storage, so the dashboard
 * stays a pure API consumer: hydrated only from POST responses (or a resume
 * GET) and never surviving a reload.
 *
 * A stateful conversation (propose / group / charter) pins its capability via
 * ``activeIntent`` so follow-up turns continue the same thread rather than
 * being re-classified: the operator answers a clarifying question or a CEO
 * interview prompt without the router re-routing mid-thread.
 */

/** Capabilities whose conversation continues across turns (pinned once opened). */
const STICKY_INTENTS: ReadonlySet<TurnIntent> = new Set<TurnIntent>([
  'propose',
  'group_convene',
  'charter',
])

interface SendOptions {
  idempotencyKey: string
  project?: string | undefined
  signal?: AbortSignal | undefined
}

export interface OrgConversationState {
  messages: readonly OrgTurn[]
  conversationId: string | undefined
  /** The pinned capability of the ongoing thread, or ``undefined`` when open. */
  activeIntent: TurnIntent | undefined
  /** True once a propose/charter conversation is closed; the input freezes. */
  conversationClosed: boolean
  /** True while a turn is in flight. */
  sending: boolean
  /** Approval ids whose in-context Approve/Decline is resolving. */
  resolvingInvites: ReadonlySet<string>
  sendTurn: (message: string, opts: SendOptions) => Promise<void>
  resolveInvite: (turnId: number, approvalId: string, accept: boolean) => void
  /** Replace the transcript wholesale (resume hydration). */
  hydrate: (patch: {
    messages: readonly OrgTurn[]
    conversationId: string | undefined
    activeIntent: TurnIntent | undefined
    conversationClosed: boolean
  }) => void
  /** Clear the thread so the next send opens a fresh conversation. */
  startNew: () => void
  /** Clear every field (test teardown; new-session reset). */
  resetAll: () => void
}

function stampNow(turns: readonly OrgTurn[], iso: string): OrgTurn[] {
  return turns.map((turn) =>
    turn.kind === 'event' || turn.kind === 'notice'
      ? turn
      : { ...turn, timestamp: turn.timestamp ?? iso },
  )
}

function buildHumanTurn(message: string, opts: SendOptions, iso: string): OrgTurn {
  return {
    id: nextMessageId(),
    kind: 'human',
    content: message,
    timestamp: iso,
    idempotencyKey: opts.idempotencyKey,
    ...(opts.project != null && { project: opts.project }),
  }
}

function errorNotice(content: string): OrgTurn {
  return { id: nextMessageId(), kind: 'notice', content, isError: true }
}

type ThreadState = Pick<
  OrgConversationState,
  'conversationId' | 'activeIntent' | 'conversationClosed'
>

// A stateful turn (propose/charter) can close its conversation; a group round
// never does. Explain/act never pin, so their prior sticky state is preserved.
// Returns ONLY the three thread fields (never the whole state) so spreading it
// into a set() cannot clobber the messages appended alongside it.
function nextThreadState(result: TurnResult, prior: ThreadState): ThreadState {
  if (!STICKY_INTENTS.has(result.intent)) {
    return {
      conversationId: prior.conversationId,
      activeIntent: prior.activeIntent,
      conversationClosed: prior.conversationClosed,
    }
  }
  const closed =
    result.propose?.conversation_closed ??
    result.charter?.conversation_closed ??
    false
  return {
    conversationId: result.conversation_id ?? prior.conversationId,
    activeIntent: result.intent,
    conversationClosed: closed,
  }
}

function turnRequestOptions(
  state: OrgConversationState,
  opts: SendOptions,
): PostTurnOptions {
  return {
    ...(state.conversationId != null && { conversationId: state.conversationId }),
    ...(state.activeIntent != null && { intentOverride: state.activeIntent }),
    ...(opts.project != null && { project: opts.project }),
    idempotencyKey: opts.idempotencyKey,
    ...(opts.signal && { signal: opts.signal }),
  }
}

function handleTurnError(
  set: (patch: Partial<OrgConversationState>) => void,
  get: () => OrgConversationState,
  err: unknown,
): void {
  if (isAbortError(err)) {
    // A deliberate operator abort is not a failure: the server still completes
    // and parks any work idempotently, so only note the detached wait.
    log.debug('Turn cancelled by user')
    set({
      messages: [
        ...get().messages,
        {
          id: nextMessageId(),
          kind: 'notice',
          content:
            'Turn cancelled. Any work the org already queued still appears in Approvals.',
        },
      ],
    })
    return
  }
  const { title, description } = describeConversationalError(
    err,
    'The org could not respond',
  )
  log.error('Turn failed', sanitizeForLog(err))
  useToastStore.getState().add({ variant: 'error', title, description })
  set({ messages: [...get().messages, errorNotice(description)] })
}

type Setter = (patch: Partial<OrgConversationState>) => void
type Getter = () => OrgConversationState

function applyBufferedResult(
  set: Setter,
  get: Getter,
  result: TurnResult,
  iso: string,
): void {
  if (result.charter) {
    useCharterStore.getState().hydrateFromTurn(result.charter)
  }
  set({
    messages: [...get().messages, ...stampNow(mapTurnResult(result), iso)],
    ...nextThreadState(result, get()),
  })
}

interface RunContext {
  message: string
  state: OrgConversationState
  opts: SendOptions
  iso: string
}

// Stream an EXPLAIN turn into one live-growing assistant bubble, then finalise
// it with the answer's sources / confidence and append any chime-ins as they
// resolve. A non-EXPLAIN turn streams nothing (the server defers it), so the
// caller re-issues it buffered. Returns the stream's outcome for that decision.
async function streamExplainOrDefer(
  set: Setter,
  get: Getter,
  ctx: RunContext,
): Promise<StreamTurnOutcome> {
  const { message, state, opts, iso } = ctx
  let streamingId: number | null = null
  let text = ''
  const ensureBubble = (): number => {
    if (streamingId !== null) return streamingId
    streamingId = nextMessageId()
    set({
      messages: [
        ...get().messages,
        { id: streamingId, kind: 'assistant', content: '', isStreaming: true, timestamp: iso },
      ],
    })
    return streamingId
  }
  const patchBubble = (patch: Partial<OrgAssistantTurn>): void => {
    const id = streamingId
    if (id === null) return
    set({
      messages: get().messages.map((turn) =>
        turn.id === id && turn.kind === 'assistant' ? { ...turn, ...patch } : turn,
      ),
    })
  }
  try {
    return await streamTurn(
      message,
      {
        onDelta: (delta) => {
          text += delta
          ensureBubble()
          patchBubble({ content: text })
        },
        onComplete: (result) => {
          const answer = result.answer
          if (!answer) return
          ensureBubble()
          patchBubble({
            content: answer.answer,
            sources: answer.sources,
            citedRecords: parseCitedRecords(answer.cited_records),
            confidence: answer.confidence,
            isStreaming: false,
          })
          // A degraded EXPLAIN (an act/convene/charter that fell below its
          // floor) gets a notice so the downgrade is never silent.
          const notice = intentDegradeNotice(result)
          set({
            ...(notice && { messages: [...get().messages, notice] }),
            ...nextThreadState(result, get()),
          })
        },
        onChime: (chime) => {
          set({
            messages: [
              ...get().messages,
              {
                id: nextMessageId(),
                kind: 'agent',
                content: chime.content,
                agentName: chime.name,
                agentRole: chime.role,
                timestamp: iso,
              },
            ],
          })
        },
      },
      turnRequestOptions(state, opts),
    )
  } finally {
    // A stream cut short (error / abort surfaced to the caller) must not strand
    // a bubble in the streaming state; clear the flag on every exit path.
    patchBubble({ isStreaming: false })
  }
}

async function runTurn(
  set: Setter,
  get: Getter,
  message: string,
  opts: SendOptions,
): Promise<void> {
  const state = get()
  if (state.sending || state.conversationClosed) return
  const iso = new Date().toISOString()
  set({
    sending: true,
    messages: [...state.messages, buildHumanTurn(message, opts, iso)],
  })
  const ctx: RunContext = { message, state, opts, iso }
  try {
    const outcome = await streamExplainOrDefer(set, get, ctx)
    if (outcome.kind === 'deferred') {
      // A side-effecting intent never runs on the stream: re-issue it against
      // the buffered, idempotent endpoint with the classified intent forced, so
      // it executes exactly once and is never re-classified.
      const result = await postTurn(message, {
        ...turnRequestOptions(state, opts),
        intentOverride: outcome.intent,
      })
      applyBufferedResult(set, get, result, iso)
    }
  } catch (err) {
    handleTurnError(set, get, err)
  } finally {
    set({ sending: false })
  }
}

async function resolveInviteImpl(
  set: (patch: Partial<OrgConversationState>) => void,
  get: () => OrgConversationState,
  turnId: number,
  approvalId: string,
  accept: boolean,
): Promise<void> {
  set({ resolvingInvites: new Set(get().resolvingInvites).add(approvalId) })
  const store = useApprovalsStore.getState()
  // approveOne / rejectOne own their error + success toast and never throw
  // (they return null on failure), so no try/catch here.
  const result = accept
    ? await store.approveOne(approvalId)
    : await store.rejectOne(approvalId, { reason: 'Declined from org chat' })
  const remaining = new Set(get().resolvingInvites)
  remaining.delete(approvalId)
  set({ resolvingInvites: remaining })
  if (!result) return
  set({
    messages: get().messages.map((turn) =>
      turn.id === turnId && turn.kind === 'event' && turn.event.type === 'invite'
        ? {
            ...turn,
            event: {
              ...turn.event,
              resolved: accept ? 'approved' : 'declined',
            },
          }
        : turn,
    ),
  })
}

function initialState(): Pick<
  OrgConversationState,
  | 'messages'
  | 'conversationId'
  | 'activeIntent'
  | 'conversationClosed'
  | 'sending'
  | 'resolvingInvites'
> {
  return {
    messages: [],
    conversationId: undefined,
    activeIntent: undefined,
    conversationClosed: false,
    sending: false,
    resolvingInvites: new Set(),
  }
}

export const useOrgConversationStore = create<OrgConversationState>()(
  (set, get) => ({
    ...initialState(),
    sendTurn: (message, opts) => runTurn(set, get, message, opts),
    resolveInvite: (turnId, approvalId, accept) =>
      void resolveInviteImpl(set, get, turnId, approvalId, accept),
    hydrate: (patch) =>
      set({
        messages: patch.messages,
        conversationId: patch.conversationId,
        activeIntent: patch.activeIntent,
        conversationClosed: patch.conversationClosed,
        sending: false,
      }),
    startNew: () => {
      useCharterStore.getState().resetInterview()
      set(initialState())
    },
    resetAll: () => set(initialState()),
  }),
)
