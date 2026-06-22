import { useCallback, useRef, useState } from 'react'

import { useMetaStore } from '@/stores/meta'

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
  /** Re-send the last user message (used by the error notice's Try again). */
  retryLast: () => void
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
    const question = input.trim()
    if (!question) return
    setInput('')
    void sendMessage(question)
  }, [input, sendMessage])

  const retryLast = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user')
    if (lastUser) void sendMessage(lastUser.content)
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
