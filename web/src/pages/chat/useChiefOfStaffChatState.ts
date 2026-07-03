import { useCallback, useEffect, useRef, useState } from 'react'

import type { AlertSummary, ChatScope, ProposalSummary } from '@/api/endpoints/meta'
import { useMetaStore } from '@/stores/meta'
import type { ChatScopeValue } from './ChatScopePicker'
import { resolveScopedRetryContent } from './scoped-retry'

export interface ChiefOfStaffMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean
}

export interface ChiefOfStaffChatState {
  messages: readonly ChiefOfStaffMessage[]
  input: string
  chatLoading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  setInput: (value: string) => void
  triggerSend: () => void
  /** Re-send the user message before the clicked error bubble's id. */
  retryLast: (beforeMsgId?: number) => void
  /**
   * Optional proposal/alert the conversation is scoped to. Persists
   * across turns (every question is scoped) until explicitly cleared
   * via `setScope(null)` -- not a one-shot "next question only" scope.
   */
  scope: ChatScopeValue | null
  setScope: (value: ChatScopeValue | null) => void
  scopeableProposals: readonly ProposalSummary[]
  scopeableAlerts: readonly AlertSummary[]
}

function toChatScope(value: ChatScopeValue | null): ChatScope | undefined {
  if (!value) return undefined
  return { kind: value.kind, id: value.id }
}

export function useChiefOfStaffChatState(): ChiefOfStaffChatState {
  const [messages, setMessages] = useState<ChiefOfStaffMessage[]>([])
  const [input, setInput] = useState('')
  const [scope, setScope] = useState<ChatScopeValue | null>(null)
  const chatLoading = useMetaStore((s) => s.chatLoading)
  const sendChat = useMetaStore((s) => s.sendChat)
  const proposals = useMetaStore((s) => s.proposals)
  const alerts = useMetaStore((s) => s.alerts)
  const fetchProposals = useMetaStore((s) => s.fetchProposals)
  const fetchAlerts = useMetaStore((s) => s.fetchAlerts)
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)

  useEffect(() => {
    if (proposals.length === 0) void fetchProposals()
    if (alerts.length === 0) void fetchAlerts()
  }, [proposals.length, alerts.length, fetchProposals, fetchAlerts])

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const sendMessage = useCallback(
    async (question: string) => {
      if (!question || chatLoading) return
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: 'user', content: question },
      ])
      const response = await sendChat(question, toChatScope(scope))
      setMessages((prev) => [...prev, buildAssistantMessage(response, nextMsgId)])
      scrollToBottom(scrollRef)
    },
    [chatLoading, sendChat, nextMsgId, scope],
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

  return {
    messages,
    input,
    chatLoading,
    scrollRef,
    setInput,
    triggerSend,
    retryLast,
    scope,
    setScope,
    scopeableProposals: proposals,
    scopeableAlerts: alerts,
  }
}

function buildAssistantMessage(
  response: Awaited<ReturnType<ReturnType<typeof useMetaStore.getState>['sendChat']>>,
  nextMsgId: () => number,
): ChiefOfStaffMessage {
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
