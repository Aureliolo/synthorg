import { useCallback, useEffect, useRef, useState } from 'react'

import {
  streamChatAnswer,
  type ChatStreamResult,
} from '@/api/endpoints/meta-stream'
import { createLogger } from '@/lib/logger'
import type { ConversationsState } from '@/stores/conversations'
import { sanitizeForLog } from '@/utils/logging'

import type { ChiefOfStaffMessage } from './chat-types'

type SetStaff = ConversationsState['setStaff']

const log = createLogger('chat:streaming')

const STREAM_FAILURE_NOTICE = 'The assistant could not respond. Please try again.'

export interface ChatStreaming {
  /** True while an answer is streaming; enables the Cancel affordance. */
  isStreaming: boolean
  /**
   * Live "a stream is in flight" read backed by the abort ref, not the
   * render-time flag, so a send guard can reject a duplicate submit that
   * races ahead of React's re-render.
   */
  isBusy: () => boolean
  /**
   * Abort the in-flight stream. Any tokens received so far are kept; if
   * none arrived the bubble shows "Stopped."
   */
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
      } catch (err) {
        const aborted = controller.signal.aborted
        if (!aborted) {
          log.error('Chat answer stream failed', sanitizeForLog(err))
        }
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
        // Guard against a newer stream having replaced this one: only the
        // controller that is still current clears the shared streaming
        // flag / ref, so an overlapping run is never torn down early.
        if (abortRef.current === controller) {
          setStreaming(false)
          abortRef.current = null
          // A stream that resolves without an onComplete frame (SSE closed
          // after deltas with no terminal event) never cleared the bubble's
          // own flag; clear it here so the typing indicator cannot stick.
          updateAssistant(assistantId, (m) =>
            m.role === 'assistant' && m.isStreaming
              ? { ...m, isStreaming: false }
              : m,
          )
        }
      }
    },
    [updateAssistant],
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])
  const isBusy = useCallback(() => abortRef.current !== null, [])

  // Abort an in-flight stream if the panel unmounts mid-answer, so the
  // fetch + reader do not outlive the component.
  useEffect(() => () => abortRef.current?.abort(), [])

  return { isStreaming: streaming, isBusy, cancel, runStream }
}
