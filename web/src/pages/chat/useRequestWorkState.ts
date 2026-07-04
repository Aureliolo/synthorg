import { useCallback, useState } from 'react'

import type { ConversationalProposeResponse } from '@/api/endpoints/meta'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'

import type { RequestWorkMessage } from './chat-types'
import { nextMessageId } from './message-id'
import { resolveScopedRetryContent } from './scoped-retry'
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
    async (message: string) => {
      if (!message || proposeLoading || conversationClosed) return
      setWork((s) => ({
        messages: [
          ...s.messages,
          { id: nextMessageId(), role: 'user', content: message },
        ],
      }))
      const conversationId = useConversationsStore.getState().work.conversationId
      const result = await propose(message, conversationId)
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
    [proposeLoading, conversationClosed, propose, setWork],
  )

  const triggerSend = useCallback(() => {
    const message = input.trim()
    // Guard before clearing: Enter during an in-flight propose (or on a
    // closed conversation) must not wipe the composed text.
    if (!message || proposeLoading || conversationClosed) return
    setInput('')
    void sendMessage(message)
  }, [input, proposeLoading, conversationClosed, sendMessage])

  const retryBefore = useCallback(
    (beforeMsgId: number) => {
      const content = resolveScopedRetryContent(
        messages,
        beforeMsgId,
        (m) => m.role === 'user',
      )
      if (content !== null) void sendMessage(content)
    },
    [messages, sendMessage],
  )

  return {
    messages,
    input,
    proposeLoading,
    conversationClosed,
    scrollRef,
    setInput,
    triggerSend,
    retryBefore,
  }
}

type Attribution = Pick<
  RequestWorkMessage,
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
