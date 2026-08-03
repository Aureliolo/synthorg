import { useCallback, useEffect, useMemo } from 'react'

import type { WsChannel } from '@/api/types/websocket'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { usePolling } from '@/hooks/usePolling'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import {
  isQuestionEvent,
  useOrgQuestionsStore,
  type OrgQuestionRecord,
} from '@/stores/org-questions'

/**
 * Keeps the chat page's open questions live.
 *
 * A question arrives on the existing ``approvals`` channel (no dedicated event
 * type: the submitted payload already carries the whole approval), with the
 * same polling belt-and-braces the Approvals page uses for a dropped socket.
 */

const QUESTION_POLL_INTERVAL = 30_000
const QUESTION_CHANNELS = ['approvals'] as const satisfies readonly WsChannel[]

export interface UseOrgQuestionsReturn {
  questions: readonly OrgQuestionRecord[]
  /**
   * Last load failure, or null. Surfaced rather than swallowed: a failed
   * hydrate renders an empty list, which is indistinguishable from "the org
   * has nothing to ask" while agents are in fact parked waiting.
   */
  error: string | null
  /** True when more open questions exist than this page shows. */
  hasMore: boolean
}

/**
 * Answering is deliberately absent from this surface: the card subscribes to
 * the store itself, so nothing has to be threaded from the page down to it.
 */
export function useOrgQuestions(): UseOrgQuestionsReturn {
  const questions = useOrgQuestionsStore((s) => s.questions)
  const error = useOrgQuestionsStore((s) => s.error)
  const hasMore = useOrgQuestionsStore((s) => s.hasMore)

  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useOrgQuestionsStore.getState().fetchQuestions()
  }, [])
  const { start, stop } = usePolling(pollFn, QUESTION_POLL_INTERVAL, { skipIfFresh })
  // ``start()`` polls once immediately, which is the mount hydrate: a reload
  // must not lose a waiting question. A separate hydrate effect alongside it
  // would issue a second request for the same page load.
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      QUESTION_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          // Only a question frame counts as freshness. The approvals channel
          // carries every approval in the org, and treating that traffic as
          // freshness would let a busy queue suppress the poll indefinitely.
          if (isQuestionEvent(event)) markFresh()
          useOrgQuestionsStore.getState().handleWsEvent(event)
        },
      })),
    [markFresh],
  )
  useWebSocket({ bindings })

  return { questions, error, hasMore }
}
