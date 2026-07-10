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
  /**
   * Live "a turn is already in flight" read (buffered load or streaming),
   * evaluated at call time so a duplicate submit racing ahead of the
   * re-render is still rejected.
   */
  isBlocked: () => boolean
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
): (
  question: string,
  idempotencyKey?: string,
  scopeOverride?: ChatScopeValue | null,
) => Promise<void> {
  const { isBlocked, scope, setStaff, sendChat, runStream } = deps
  return useCallback(
    async (
      question: string,
      idempotencyKey?: string,
      scopeOverride?: ChatScopeValue | null,
    ) => {
      if (!question || isBlocked()) return
      // A retry replays the scope the turn was minted against (passed
      // explicitly, so ``null`` re-runs an unscoped turn); a fresh send uses
      // the live picker. Reusing the key with a scope the operator changed
      // afterwards would pair one key with two different requests.
      const effectiveScope = scopeOverride !== undefined ? scopeOverride : scope
      // Mint the key once per logical turn and store it (plus the scope
      // snapshot) on the user turn so a manual retry of the buffered scoped
      // path reuses both (a turn that succeeded server-side is deduped, not
      // re-run). The unscoped streaming path below ignores the key: a token
      // stream cannot be replayed from cache, so streaming and idempotency
      // are mutually exclusive and a streamed retry genuinely re-runs.
      const key = idempotencyKey ?? crypto.randomUUID()
      setStaff((s) => ({
        messages: [
          ...s.messages,
          {
            id: nextMessageId(),
            role: 'user',
            content: question,
            idempotencyKey: key,
            scope: effectiveScope,
          },
        ],
      }))
      if (effectiveScope) {
        const response = await sendChat(question, toChatScope(effectiveScope), key)
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
    [isBlocked, scope, setStaff, sendChat, runStream],
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
      citedRecords: response.cited_records,
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
