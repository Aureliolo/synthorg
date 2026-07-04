import { useCallback, useRef, useState } from 'react'

import {
  streamChatAnswer,
  type ChatStreamResult,
} from '@/api/endpoints/meta-stream'
import type { ConversationsState } from '@/stores/conversations'

import type { ChiefOfStaffMessage } from './chat-types'

type SetStaff = ConversationsState['setStaff']

const STREAM_FAILURE_NOTICE = 'The assistant could not respond. Please try again.'

export interface ChatStreaming {
  /** True while an answer is streaming; enables the Cancel affordance. */
  isStreaming: boolean
  /** Abort the in-flight stream; the partial answer is kept. */
  cancel: () => void
  /** Stream one free-form answer into the given assistant bubble id. */
  runStream: (question: string, assistantId: number) => Promise<void>
}

/**
 * Owns the token-streaming lifecycle for the Chief-of-Staff free-form
 * path: the streaming flag, the per-turn ``AbortController``, and the
 * incremental writes into a single assistant bubble. Kept out of
 * ``useChiefOfStaffChatState`` so that hook stays under its line budget.
 */
export function useChatStreaming(setStaff: SetStaff): ChatStreaming {
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const updateAssistant = useCallback(
    (id: number, update: (m: ChiefOfStaffMessage) => ChiefOfStaffMessage) =>
      setStaff((s) => ({
        messages: s.messages.map((m) => (m.id === id ? update(m) : m)),
      })),
    [setStaff],
  )

  const runStream = useCallback(
    async (question: string, assistantId: number) => {
      const controller = new AbortController()
      abortRef.current = controller
      setStreaming(true)
      try {
        await streamChatAnswer(
          question,
          {
            onDelta: (delta) =>
              updateAssistant(assistantId, (m) => ({
                ...m,
                content: m.content + delta,
              })),
            onComplete: (result: ChatStreamResult) =>
              updateAssistant(assistantId, (m) => ({
                ...m,
                content: result.answer || m.content,
                sources: result.sources,
                confidence: result.confidence,
                isStreaming: false,
              })),
          },
          controller.signal,
        )
      } catch {
        const aborted = controller.signal.aborted
        updateAssistant(assistantId, (m) =>
          aborted
            ? { ...m, isStreaming: false, content: m.content || 'Stopped.' }
            : {
                ...m,
                content: STREAM_FAILURE_NOTICE,
                isStreaming: false,
                isError: true,
              },
        )
      } finally {
        setStreaming(false)
        abortRef.current = null
      }
    },
    [updateAssistant],
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { isStreaming: streaming, cancel, runStream }
}
