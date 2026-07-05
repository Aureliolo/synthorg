import { useCallback, useState } from 'react'

import type { ConversationalProposeResponse } from '@/api/endpoints/meta'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'

import type { RequestWorkMessage } from './chat-types'
import { nextMessageId } from './message-id'
import { resolveScopedRetryTarget } from './scoped-retry'
import { useScrollToBottom } from './use-scroll-to-bottom'

export type {
  RequestWorkMessage,
  RequestWorkProposal,
  RequestWorkSteering,
} from './chat-types'

export interface RequestWorkState {
  messages: readonly RequestWorkMessage[]
  input: string
  proposeLoading: boolean
  /** True once the backend closes the conversation; the input is disabled. */
  conversationClosed: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  setInput: (value: string) => void
  triggerSend: () => void
  retryBefore: (beforeMsgId: number) => void
  /** Clear a closed conversation so the next send opens a fresh one. */
  startNew: () => void
}

export function useRequestWorkState(): RequestWorkState {
  const messages = useConversationsStore((s) => s.work.messages)
  const conversationClosed = useConversationsStore((s) => s.work.closed)
  const setWork = useConversationsStore((s) => s.setWork)
  const [input, setInput] = useState('')
  const proposeLoading = useMetaStore((s) => s.proposeLoading)
  const propose = useMetaStore((s) => s.proposeConversation)
  const scrollRef = useScrollToBottom(messages)

  const sendMessage = useCallback(
    async (message: string, idempotencyKey?: string) => {
      // Read the live loading flag (not the render-time closure) so a rapid
      // second submit in the same render window can't slip past a propose
      // that is already in flight.
      if (!message || useMetaStore.getState().proposeLoading || conversationClosed)
        return
      // Mint the key once per logical turn; a manual retry reuses it so a
      // parked proposal that actually succeeded is deduped, not re-parked.
      const key = idempotencyKey ?? crypto.randomUUID()
      setWork((s) => ({
        messages: [
          ...s.messages,
          {
            id: nextMessageId(),
            role: 'user',
            content: message,
            idempotencyKey: key,
          },
        ],
      }))
      const conversationId = useConversationsStore.getState().work.conversationId
      const result = await propose(message, conversationId, key)
      if (result) {
        setWork({
          conversationId: result.conversation_id,
          closed: result.conversation_closed,
        })
      }
      setWork((s) => ({
        messages: [...s.messages, buildAssistantMessage(result)],
      }))
    },
    [conversationClosed, propose, setWork],
  )

  const triggerSend = useCallback(() => {
    const message = input.trim()
    // Guard before clearing: Enter during an in-flight propose (or on a
    // closed conversation) must not wipe the composed text.
    if (!message || useMetaStore.getState().proposeLoading || conversationClosed)
      return
    setInput('')
    void sendMessage(message)
  }, [input, conversationClosed, sendMessage])

  const retryBefore = useCallback(
    (beforeMsgId: number) => {
      const target = resolveScopedRetryTarget(
        messages,
        beforeMsgId,
        (m) => m.role === 'user',
      )
      if (target) void sendMessage(target.content, target.idempotencyKey)
    },
    [messages, sendMessage],
  )

  const startNew = useCallback(() => {
    setInput('')
    // A fresh conversation: dropping conversationId makes the next send
    // open a new one server-side, and clearing closed re-enables the input.
    setWork({ messages: [], conversationId: undefined, closed: false })
  }, [setWork])

  return {
    messages,
    input,
    proposeLoading,
    conversationClosed,
    scrollRef,
    setInput,
    triggerSend,
    retryBefore,
    startNew,
  }
}

type Attribution = Pick<
  Extract<RequestWorkMessage, { role: 'assistant' }>,
  'responderRole' | 'responderName' | 'routedTopic'
>

function toAttribution(result: ConversationalProposeResponse): Attribution {
  return {
    ...(result.responder_role != null && { responderRole: result.responder_role }),
    ...(result.responder_name != null && { responderName: result.responder_name }),
    ...(result.routed_topic != null && { routedTopic: result.routed_topic }),
  }
}

function buildFailureMessage(): RequestWorkMessage {
  return {
    id: nextMessageId(),
    role: 'assistant',
    content: 'The assistant could not respond. Please try again.',
    isError: true,
  }
}

function buildAssistantMessage(
  result: ConversationalProposeResponse | null,
): RequestWorkMessage {
  if (!result) {
    return buildFailureMessage()
  }
  if (result.status === 'needs_clarification') {
    return {
      id: nextMessageId(),
      role: 'assistant',
      content: result.clarifying_question ?? 'Could you clarify?',
      ...toAttribution(result),
    }
  }
  const proposals = result.proposals.map((p) => ({
    title: p.title,
    approvalId: p.approval_id,
  }))
  const steering = result.steering.map((s) => ({
    text: s.text,
    approvalId: s.approval_id,
  }))
  // Count both branches: a turn that parks only steering directives would
  // otherwise read "Queued 0 work items" and hide real queued work.
  const total = proposals.length + steering.length
  const plural = total === 1 ? '' : 's'
  return {
    id: nextMessageId(),
    role: 'assistant',
    content: `Queued ${total} item${plural} for your approval.`,
    proposals,
    steering,
    ...toAttribution(result),
  }
}
