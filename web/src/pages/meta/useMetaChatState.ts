import { useCallback, useRef, useState } from 'react'

import { useMetaStore } from '@/stores/meta'

export interface MetaChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number
}

export interface MetaChatState {
  messages: readonly MetaChatMessage[]
  input: string
  chatLoading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  setInput: (value: string) => void
  triggerSend: () => void
}

export function useMetaChatState(): MetaChatState {
  const [messages, setMessages] = useState<MetaChatMessage[]>([])
  const [input, setInput] = useState('')
  const chatLoading = useMetaStore((s) => s.chatLoading)
  const sendChat = useMetaStore((s) => s.sendChat)
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const handleSend = useCallback(async () => {
    const question = input.trim()
    if (!question || chatLoading) return
    setInput('')
    setMessages((prev) => [
      ...prev,
      { id: nextMsgId(), role: 'user', content: question },
    ])
    const response = await sendChat(question)
    setMessages((prev) => [...prev, buildAssistantMessage(response, nextMsgId)])
    scrollToBottom(scrollRef)
  }, [input, chatLoading, sendChat, nextMsgId])

  const triggerSend = useCallback(() => void handleSend(), [handleSend])

  return { messages, input, chatLoading, scrollRef, setInput, triggerSend }
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
  const errMsg = useMetaStore.getState().error
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: errMsg
      ? `Chat request failed: ${errMsg}`
      : 'Failed to get a response. Please try again.',
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
