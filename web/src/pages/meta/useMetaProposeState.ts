import { useCallback, useRef, useState } from 'react'

import type { ConversationalProposeResponse } from '@/api/endpoints/meta'
import { useMetaStore } from '@/stores/meta'

export interface MetaProposeMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  /** Role of the routed agent that answered, when concern-routed. */
  responderRole?: string
  /** Display name of the routed agent, when concern-routed. */
  responderName?: string
  /** Concern topic that selected the role, when routed. */
  routedTopic?: string
  /** Titles of parked work items, on the "proposed" branch. */
  proposals?: readonly string[]
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean
}

export interface MetaProposeState {
  messages: readonly MetaProposeMessage[]
  input: string
  proposeLoading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  setInput: (value: string) => void
  triggerSend: () => void
  retryLast: () => void
}

export function useMetaProposeState(): MetaProposeState {
  const [messages, setMessages] = useState<MetaProposeMessage[]>([])
  const [input, setInput] = useState('')
  const proposeLoading = useMetaStore((s) => s.proposeLoading)
  const propose = useMetaStore((s) => s.proposeConversation)
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)
  const conversationIdRef = useRef<string | undefined>(undefined)

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const sendMessage = useCallback(
    async (message: string) => {
      if (!message || proposeLoading) return
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: 'user', content: message },
      ])
      const result = await propose(message, conversationIdRef.current)
      if (result) conversationIdRef.current = result.conversation_id
      setMessages((prev) => [...prev, buildAssistantMessage(result, nextMsgId)])
      scrollToBottom(scrollRef)
    },
    [proposeLoading, propose, nextMsgId],
  )

  const triggerSend = useCallback(() => {
    const message = input.trim()
    if (!message) return
    setInput('')
    void sendMessage(message)
  }, [input, sendMessage])

  const retryLast = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user')
    if (lastUser) void sendMessage(lastUser.content)
  }, [messages, sendMessage])

  return { messages, input, proposeLoading, scrollRef, setInput, triggerSend, retryLast }
}

type Attribution = Pick<
  MetaProposeMessage,
  'responderRole' | 'responderName' | 'routedTopic'
>

function toAttribution(result: ConversationalProposeResponse): Attribution {
  return {
    ...(result.responder_role != null && { responderRole: result.responder_role }),
    ...(result.responder_name != null && { responderName: result.responder_name }),
    ...(result.routed_topic != null && { routedTopic: result.routed_topic }),
  }
}

function buildFailureMessage(nextMsgId: () => number): MetaProposeMessage {
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
): MetaProposeMessage {
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
  const titles = result.proposals.map((p) => p.title)
  const plural = titles.length === 1 ? '' : 's'
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: `Queued ${titles.length} work item${plural} for your approval.`,
    proposals: titles,
    ...toAttribution(result),
  }
}

function scrollToBottom(scrollRef: React.RefObject<HTMLDivElement | null>): void {
  requestAnimationFrame(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  })
}
