import { useCallback, useEffect, useState } from 'react'

import type { AlertSummary, ChatScope, ProposalSummary } from '@/api/endpoints/meta'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'
import type { ChiefOfStaffMessage } from './chat-types'
import type { ChatScopeValue } from './ChatScopePicker'
import { nextMessageId } from './message-id'
import { resolveScopedRetryTarget } from './scoped-retry'
import { useScrollToBottom } from './use-scroll-to-bottom'

export type { ChiefOfStaffMessage } from './chat-types'

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
  const messages = useConversationsStore((s) => s.staff.messages)
  const scope = useConversationsStore((s) => s.staff.scope)
  const setStaff = useConversationsStore((s) => s.setStaff)
  const [input, setInput] = useState('')
  const chatLoading = useMetaStore((s) => s.chatLoading)
  const sendChat = useMetaStore((s) => s.sendChat)
  const proposals = useMetaStore((s) => s.proposals)
  const alerts = useMetaStore((s) => s.alerts)
  const fetchProposals = useMetaStore((s) => s.fetchProposals)
  const fetchAlerts = useMetaStore((s) => s.fetchAlerts)
  const scrollRef = useScrollToBottom(messages)

  useEffect(() => {
    if (proposals.length === 0) void fetchProposals()
    if (alerts.length === 0) void fetchAlerts()
  }, [proposals.length, alerts.length, fetchProposals, fetchAlerts])

  const setScope = useCallback(
    (value: ChatScopeValue | null) => setStaff({ scope: value }),
    [setStaff],
  )

  const sendMessage = useCallback(
    async (question: string, idempotencyKey?: string) => {
      if (!question || chatLoading) return
      // Mint the key once per logical turn; a manual retry reuses it so a
      // turn that actually succeeded server-side is deduped, not re-run.
      const key = idempotencyKey ?? crypto.randomUUID()
      setStaff((s) => ({
        messages: [
          ...s.messages,
          {
            id: nextMessageId(),
            role: 'user',
            content: question,
            idempotencyKey: key,
          },
        ],
      }))
      const response = await sendChat(question, toChatScope(scope), key)
      setStaff((s) => ({
        messages: [...s.messages, buildAssistantMessage(response)],
      }))
    },
    [chatLoading, sendChat, scope, setStaff],
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
  const retryLast = useCallback(
    (beforeMsgId?: number) => {
      const target = resolveScopedRetryTarget(
        messages,
        beforeMsgId,
        (m) => m.role === 'user',
      )
      if (target) void sendMessage(target.content, target.idempotencyKey)
    },
    [messages, sendMessage],
  )

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
): ChiefOfStaffMessage {
  if (response) {
    return {
      id: nextMessageId(),
      role: 'assistant',
      content: response.answer,
      sources: response.sources,
      confidence: response.confidence,
    }
  }
  return {
    id: nextMessageId(),
    role: 'assistant',
    content: 'The assistant could not respond. Please try again.',
    isError: true,
  }
}
