import { useCallback, useRef, useState } from 'react'

import { useMetaStore } from '@/stores/meta'
import { resolveScopedRetryContent } from './scoped-retry'

export interface MetaChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean
}

export interface MetaChatState {
  messages: readonly MetaChatMessage[]
  input: string
  chatLoading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  setInput: (value: string) => void
  triggerSend: () => void
  /** Re-send the user message before the clicked error bubble's id. */
  retryLast: (beforeMsgId?: number) => void
}

export function useMetaChatState(): MetaChatState {
  const [messages, setMessages] = useState<MetaChatMessage[]>([])
  const [input, setInput] = useState('')
  const chatLoading = useMetaStore((s) => s.chatLoading)
  const sendChat = useMetaStore((s) => s.sendChat)
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const sendMessage = useCallback(
    async (question: string) => {
      if (!question || chatLoading) return
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: 'user', content: question },
      ])
      const response = await sendChat(question)
      setMessages((prev) => [...prev, buildAssistantMessage(response, nextMsgId)])
      scrollToBottom(scrollRef)
    },
    [chatLoading, sendChat, nextMsgId],
  )

  const triggerSend = useCallback(() => {
    // Mirror sendMessage's loading guard before clearing the input, so a send
    // blocked by an in-flight turn does not discard the user's composed text.
    if (chatLoading) return
    const question = input.trim()
    if (!question) return
    setInput('')
    void sendMessage(question)
  }, [chatLoading, input, sendMessage])

  // Retry the user message that precedes the clicked error bubble (see
  // ``resolveScopedRetryContent``); an unscoped retry would resend the wrong
  // turn when multiple failures exist.
  const retryLast = useCallback((beforeMsgId?: number) => {
    const content = resolveScopedRetryContent(messages, beforeMsgId, (m) => m.role === 'user')
    if (content !== null) void sendMessage(content)
  }, [messages, sendMessage])

  return { messages, input, chatLoading, scrollRef, setInput, triggerSend, retryLast }
}

function buildAssistantMessage(
  response: Awaited<ReturnType<ReturnType<typeof useMetaStore.getState>['sendChat']>>,
  nextMsgId: () => number,
): MetaChatMessage {
  if (response) {
    return {
      id: nextMsgId(),
      role: 'assistant',
      content: response.answer,
      sources: response.sources,
      confidence: response.confidence,
    }
  }
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: 'The assistant could not respond. Please try again.',
    isError: true,
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
