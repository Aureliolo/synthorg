import { useCallback, useEffect, useRef, useState } from 'react'

import { useOrgConversationStore } from '@/stores/org-conversation'

import type { OrgHumanTurn, OrgTurn } from './org-chat-types'
import { useAutoScroll } from './use-auto-scroll'

/**
 * The human turn preceding ``beforeTurnId`` (exclusive), or the most recent
 * one when it is undefined. "Try again" on an error bubble must replay the
 * message that preceded THAT bubble, not the transcript tail: with several
 * failed turns an unscoped retry would resend the wrong one.
 */
function humanTurnBefore(
  messages: readonly OrgTurn[],
  beforeTurnId: number,
): OrgHumanTurn | null {
  const cutoff = messages.findIndex((m) => m.id === beforeTurnId)
  const scoped = cutoff < 0 ? messages : messages.slice(0, cutoff)
  for (let i = scoped.length - 1; i >= 0; i -= 1) {
    const turn = scoped[i]
    if (turn?.kind === 'human') return turn
  }
  return null
}

/**
 * Owns the operator side of the one unified org conversation: the composer
 * input, the per-turn abort controller (Cancel), retry-target resolution, and
 * the smart auto-scroll. The transcript, the sticky-intent threading, and the
 * send call itself live in {@link useOrgConversationStore}; this hook is the
 * thin interaction layer over it.
 */
export interface OrgConversation {
  messages: readonly OrgTurn[]
  input: string
  sending: boolean
  conversationClosed: boolean
  autoScroll: ReturnType<typeof useAutoScroll>
  resolvingInvites: ReadonlySet<string>
  setInput: (value: string) => void
  triggerSend: () => void
  /** Abort the in-flight turn; the server still completes idempotently. */
  cancel: () => void
  /** Re-send the human turn that precedes the clicked error bubble. */
  retry: (beforeTurnId: number) => void
  resolveInvite: (turnId: number, approvalId: string, accept: boolean) => void
  /** Clear the thread so the next send opens a fresh conversation. */
  startNew: () => void
}

export function useOrgConversation(): OrgConversation {
  const messages = useOrgConversationStore((s) => s.messages)
  const sending = useOrgConversationStore((s) => s.sending)
  const conversationClosed = useOrgConversationStore((s) => s.conversationClosed)
  const resolvingInvites = useOrgConversationStore((s) => s.resolvingInvites)
  const sendTurn = useOrgConversationStore((s) => s.sendTurn)
  const resolveInvite = useOrgConversationStore((s) => s.resolveInvite)
  const startNew = useOrgConversationStore((s) => s.startNew)

  const [input, setInput] = useState('')
  const autoScroll = useAutoScroll(messages)
  const abortRef = useRef<AbortController | null>(null)
  // `retry` reads the transcript through a ref so its identity stays stable
  // across the per-token `messages` churn of a streaming answer; a `messages`
  // dependency would re-create it on every delta and re-render the whole
  // memoized transcript.
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  // Abort an in-flight turn if the page unmounts mid-send, so the fetch does
  // not outlive the component.
  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(
    (message: string, idempotencyKey: string, project?: string) => {
      const controller = new AbortController()
      abortRef.current = controller
      void sendTurn(message, {
        idempotencyKey,
        ...(project != null && { project }),
        signal: controller.signal,
      }).finally(() => {
        if (abortRef.current === controller) abortRef.current = null
      })
    },
    [sendTurn],
  )

  const triggerSend = useCallback(() => {
    // Guard on the live store flags (not the render-time closure) so a rapid
    // second submit cannot slip past an in-flight turn or a closed thread, and
    // the composed text is never discarded when a send is blocked.
    const state = useOrgConversationStore.getState()
    if (state.sending || state.conversationClosed) return
    const message = input.trim()
    if (!message) return
    setInput('')
    send(message, crypto.randomUUID())
  }, [input, send])

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  const retry = useCallback(
    (beforeTurnId: number) => {
      const target = humanTurnBefore(messagesRef.current, beforeTurnId)
      if (target) {
        // Reuse the original key so a turn that actually succeeded server-side
        // is deduped rather than re-run, and replay the project it was minted
        // against.
        send(target.content, target.idempotencyKey ?? crypto.randomUUID(), target.project)
      }
    },
    [send],
  )

  return {
    messages,
    input,
    sending,
    conversationClosed,
    autoScroll,
    resolvingInvites,
    setInput,
    triggerSend,
    cancel,
    retry,
    resolveInvite,
    startNew,
  }
}
