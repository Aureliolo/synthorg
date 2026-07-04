import { useCallback } from 'react'

import type { ChatScope } from '@/api/endpoints/meta'
import type { ConversationsState } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'

import type { ChiefOfStaffMessage } from './chat-types'
import type { ChatScopeValue } from './ChatScopePicker'
import { nextMessageId } from './message-id'

type SetStaff = ConversationsState['setStaff']
type SendChat = ReturnType<typeof useMetaStore.getState>['sendChat']

interface SendDeps {
  /** True when a turn is already in flight (buffered load or streaming). */
  blocked: boolean
  scope: ChatScopeValue | null
  setStaff: SetStaff
  sendChat: SendChat
  runStream: (question: string, assistantId: number) => Promise<void>
}

function toChatScope(value: ChatScopeValue | null): ChatScope | undefined {
  if (!value) return undefined
  return { kind: value.kind, id: value.id }
}

/**
 * The Chief-of-Staff send action: append the user turn, then either take
 * the buffered dedicated-explain path (when scoped) or stream tokens into
 * a fresh assistant bubble (unscoped free-form). Extracted so the state
 * hook stays under its line budget.
 */
export function useSendChiefOfStaff(
  deps: SendDeps,
): (question: string, idempotencyKey?: string) => Promise<void> {
  const { blocked, scope, setStaff, sendChat, runStream } = deps
  return useCallback(
    async (question: string, idempotencyKey?: string) => {
      if (!question || blocked) return
      // Mint the key once per logical turn; a manual retry reuses it so a
      // turn that actually succeeded server-side is deduped, not re-run.
      const key = idempotencyKey ?? crypto.randomUUID()
      setStaff((s) => ({
        messages: [
          ...s.messages,
          { id: nextMessageId(), role: 'user', content: question, idempotencyKey: key },
        ],
      }))
      if (scope) {
        const response = await sendChat(question, toChatScope(scope), key)
        setStaff((s) => ({
          messages: [...s.messages, buildAssistantMessage(response)],
        }))
        return
      }
      const assistantId = nextMessageId()
      setStaff((s) => ({
        messages: [
          ...s.messages,
          { id: assistantId, role: 'assistant', content: '', isStreaming: true },
        ],
      }))
      await runStream(question, assistantId)
    },
    [blocked, scope, setStaff, sendChat, runStream],
  )
}

function buildAssistantMessage(
  response: Awaited<ReturnType<SendChat>>,
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
