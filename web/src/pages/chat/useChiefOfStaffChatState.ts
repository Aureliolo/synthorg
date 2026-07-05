import { useCallback, useEffect, useState } from 'react'

import type { AlertSummary, ProposalSummary } from '@/api/endpoints/meta'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'
import type { ChiefOfStaffMessage } from './chat-types'
import type { ChatScopeValue } from './ChatScopePicker'
import { resolveScopedRetryTarget } from './scoped-retry'
import { useChatStreaming } from './use-chat-streaming'
import { useSendChiefOfStaff } from './use-chief-of-staff-send'
import { useScrollToBottom } from './use-scroll-to-bottom'

export type { ChiefOfStaffMessage } from './chat-types'

export interface ChiefOfStaffChatState {
  messages: readonly ChiefOfStaffMessage[]
  input: string
  chatLoading: boolean
  /** True while an unscoped answer is streaming; enables the Cancel affordance. */
  isStreaming: boolean
  /** Abort the in-flight stream; the partial answer is kept. */
  cancel: () => void
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

export function useChiefOfStaffChatState(): ChiefOfStaffChatState {
  const messages = useConversationsStore((s) => s.staff.messages)
  const scope = useConversationsStore((s) => s.staff.scope)
  const setStaff = useConversationsStore((s) => s.setStaff)
  const [input, setInput] = useState('')
  const { isStreaming, isBusy, cancel, runStream } = useChatStreaming(setStaff)
  const metaChatLoading = useMetaStore((s) => s.chatLoading)
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

  // Live block read: the store's chatLoading (buffered scoped path) or an
  // in-flight stream, evaluated at call time so a duplicate submit racing
  // ahead of the re-render is rejected.
  const isBlocked = useCallback(
    () => useMetaStore.getState().chatLoading || isBusy(),
    [isBusy],
  )

  const sendMessage = useSendChiefOfStaff({
    isBlocked,
    scope,
    setStaff,
    sendChat,
    runStream,
  })

  const triggerSend = useCallback(() => {
    if (isBlocked()) return
    const question = input.trim()
    if (!question) return
    setInput('')
    void sendMessage(question)
  }, [isBlocked, input, sendMessage])

  // Retry the user message that precedes the clicked error bubble; an
  // unscoped retry would resend the wrong turn when multiple failures exist.
  const retryLast = useCallback(
    (beforeMsgId?: number) => {
      const target = resolveScopedRetryTarget(
        messages,
        beforeMsgId,
        (m) => m.role === 'user',
      )
      if (target && target.role === 'user') {
        void sendMessage(target.content, target.idempotencyKey, target.scope)
      }
    },
    [messages, sendMessage],
  )

  const last = messages.at(-1)
  const awaitingFirstToken =
    isStreaming && last?.role === 'assistant' && last.content === ''

  return {
    messages,
    input,
    chatLoading: metaChatLoading || awaitingFirstToken,
    isStreaming,
    cancel,
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
