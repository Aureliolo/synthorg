import { useCallback, useRef, useState } from 'react'

import type { ConversationalProposeResponse } from '@/api/endpoints/meta'
import { useMetaStore } from '@/stores/meta'

import { resolveScopedRetryContent } from './scoped-retry'
import { useScrollToBottom } from './use-scroll-to-bottom'

/** A parked work item, with its approval id for a deep link. */
export interface RequestWorkProposal {
  title: string
  approvalId: string
}

/** A parked steering directive, with its approval id for a deep link. */
export interface RequestWorkSteering {
  text: string
  approvalId: string
}

export interface RequestWorkMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  /** Role of the routed agent that answered, when concern-routed. */
  responderRole?: string | undefined
  /** Display name of the routed agent, when concern-routed. */
  responderName?: string | undefined
  /** Concern topic that selected the role, when routed. */
  routedTopic?: string | undefined
  /** Parked work items, on the "proposed" branch. */
  proposals?: readonly RequestWorkProposal[] | undefined
  /** Parked steering directives, on the "proposed" branch. */
  steering?: readonly RequestWorkSteering[] | undefined
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean | undefined
}

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
  const [messages, setMessages] = useState<RequestWorkMessage[]>([])
  const [input, setInput] = useState('')
  const [conversationClosed, setConversationClosed] = useState(false)
  const proposeLoading = useMetaStore((s) => s.proposeLoading)
  const propose = useMetaStore((s) => s.proposeConversation)
  const scrollRef = useScrollToBottom(messages)
  const msgIdRef = useRef(0)
  const conversationIdRef = useRef<string | undefined>(undefined)

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const sendMessage = useCallback(
    async (message: string) => {
      if (!message || proposeLoading || conversationClosed) return
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: 'user', content: message },
      ])
      const result = await propose(message, conversationIdRef.current)
      if (result) {
        conversationIdRef.current = result.conversation_id
        setConversationClosed(result.conversation_closed)
      }
      setMessages((prev) => [...prev, buildAssistantMessage(result, nextMsgId)])
    },
    [proposeLoading, conversationClosed, propose, nextMsgId],
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

function buildFailureMessage(nextMsgId: () => number): RequestWorkMessage {
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: 'The assistant could not respond. Please try again.',
    isError: true,
  }
}

function buildAssistantMessage(
  result: ConversationalProposeResponse | null,
  nextMsgId: () => number,
): RequestWorkMessage {
  if (!result) {
    return buildFailureMessage(nextMsgId)
  }
  if (result.status === 'needs_clarification') {
    return {
      id: nextMsgId(),
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
    id: nextMsgId(),
    role: 'assistant',
    content: `Queued ${total} item${plural} for your approval.`,
    proposals,
    steering,
    ...toAttribution(result),
  }
}
