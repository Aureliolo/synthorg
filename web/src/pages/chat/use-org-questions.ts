import { useCallback, useEffect, useMemo } from 'react'

import type { WsChannel } from '@/api/types/websocket'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { usePolling } from '@/hooks/usePolling'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import {
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
  resolving: ReadonlySet<string>
  answer: (approvalId: string, answer: string, chosenOptionId?: string) => void
  decline: (approvalId: string) => void
}

export function useOrgQuestions(): UseOrgQuestionsReturn {
  const questions = useOrgQuestionsStore((s) => s.questions)
  const resolving = useOrgQuestionsStore((s) => s.resolving)

  // Hydrate on mount: a reload must not lose a waiting question.
  useEffect(() => {
    void useOrgQuestionsStore.getState().fetchQuestions()
  }, [])

  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useOrgQuestionsStore.getState().fetchQuestions()
  }, [])
  const { start, stop } = usePolling(pollFn, QUESTION_POLL_INTERVAL, { skipIfFresh })
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      QUESTION_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          markFresh()
          useOrgQuestionsStore.getState().handleWsEvent(event)
        },
      })),
    [markFresh],
  )
  useWebSocket({ bindings })

  const answer = useCallback(
    (approvalId: string, text: string, chosenOptionId?: string) => {
      void useOrgQuestionsStore
        .getState()
        .answerQuestion(approvalId, text, chosenOptionId)
    },
    [],
  )
  const decline = useCallback((approvalId: string) => {
    void useOrgQuestionsStore.getState().declineQuestion(approvalId)
  }, [])

  return { questions, resolving, answer, decline }
}
