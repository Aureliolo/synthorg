import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  ActiveAgentSummary,
  ConversationalActResult,
  TerminationReason,
} from '@/api/types'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'
import type { ActMessage } from './chat-types'
import { nextMessageId } from './message-id'
import { resolveScopedRetryContent } from './scoped-retry'
import { useScrollToBottom } from './use-scroll-to-bottom'

export type { ActMessage } from './chat-types'

export interface DirectActionState {
  activeAgents: readonly ActiveAgentSummary[]
  selectedAgentId: string | null
  messages: readonly ActMessage[]
  input: string
  loading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  selectAgent: (id: string) => void
  setInput: (value: string) => void
  triggerSend: () => void
  retryLast: (beforeMsgId?: number) => void
}

export function useDirectActionState(): DirectActionState {
  const activeAgents = useMetaStore((s) => s.activeAgents)
  const loading = useMetaStore((s) => s.actionLoading)
  const runAction = useMetaStore((s) => s.runAction)
  const fetchActiveAgents = useMetaStore((s) => s.fetchActiveAgents)

  const messages = useConversationsStore((s) => s.action.messages)
  const selectedAgentId = useConversationsStore((s) => s.action.selectedAgentId)
  const setAction = useConversationsStore((s) => s.setAction)
  const [input, setInput] = useState('')
  const scrollRef = useScrollToBottom(messages)

  const fetchRef = useRef(fetchActiveAgents)
  fetchRef.current = fetchActiveAgents
  useEffect(() => {
    void fetchRef.current()
  }, [])

  const selectAgent = useCallback(
    (id: string) =>
      setAction((s) => ({
        selectedAgentId: s.selectedAgentId === id ? null : id,
      })),
    [setAction],
  )

  const sendInstruction = useCallback(
    async (instruction: string) => {
      if (!instruction || loading || !selectedAgentId) return
      setAction((s) => ({
        messages: [
          ...s.messages,
          { id: nextMessageId(), kind: 'human', content: instruction },
        ],
      }))
      const conversationId =
        useConversationsStore.getState().action.conversationId
      const result = await runAction(instruction, selectedAgentId, conversationId)
      // The acting agent is the one the operator selected; resolve its role
      // from the roster rather than mislabelling every action as "acting".
      const actingRole = activeAgents.find((a) => a.id === selectedAgentId)?.role
      setAction((s) => ({
        messages: [...s.messages, buildActMessage(result, actingRole)],
        ...(result && { conversationId: result.conversation_id ?? undefined }),
      }))
    },
    [loading, selectedAgentId, runAction, activeAgents, setAction],
  )

  // ``runAction`` owns its error UX (catches internally, returns ``null`` on
  // failure), so voiding the promise is safe -- there is no rejection to leak.
  const triggerSend = useCallback(() => {
    // Mirror sendInstruction's preconditions before clearing the input, so a
    // send blocked by an in-flight action or a missing agent selection does not
    // discard the operator's composed text.
    if (loading || !selectedAgentId) return
    const instruction = input.trim()
    if (!instruction) return
    setInput('')
    void sendInstruction(instruction)
  }, [loading, selectedAgentId, input, sendInstruction])

  // Retry the human instruction that precedes the clicked error bubble (see
  // ``resolveScopedRetryContent``); an unscoped retry would replay the
  // transcript tail rather than the instruction the operator clicked on.
  const retryLast = useCallback(
    (beforeMsgId?: number) => {
      const content = resolveScopedRetryContent(
        messages,
        beforeMsgId,
        (m) => m.kind === 'human',
      )
      if (content !== null) void sendInstruction(content)
    },
    [messages, sendInstruction],
  )

  return {
    activeAgents,
    selectedAgentId,
    messages,
    input,
    loading,
    scrollRef,
    selectAgent,
    setInput,
    triggerSend,
    retryLast,
  }
}

// Copy for a non-clean stop. ``completed``/``parked`` already carry their
// own message or approval block, so they render nothing extra; the rest
// keep an action bubble from ever appearing blank.
const TERMINATION_REASON_COPY: Readonly<
  Record<TerminationReason, string | null>
> = {
  completed: null,
  parked: null,
  max_turns: 'Stopped: reached the turn limit.',
  budget_exhausted: 'Stopped: the action budget was exhausted.',
  stagnation: 'Stopped: no further progress was possible.',
  shutdown: 'Stopped: the runtime shut down.',
  cancelled: 'Stopped: the action was cancelled.',
  error: 'Stopped: an error interrupted the action.',
}

function buildActMessage(
  result: ConversationalActResult | null,
  actingRole: string | undefined,
): ActMessage {
  if (!result) {
    return {
      id: nextMessageId(),
      kind: 'notice',
      content: 'The action could not be completed. Please try again.',
      isError: true,
    }
  }
  const action = result.action
  const finalMessage = action.final_message ?? ''
  // Never render an empty action bubble: fall back to the stop-reason copy
  // and then a generic line so there is always something to read.
  const content =
    finalMessage !== ''
      ? finalMessage
      : (TERMINATION_REASON_COPY[action.termination_reason] ??
        'The action finished with no message.')
  return {
    id: nextMessageId(),
    kind: 'action',
    content,
    agentName: result.agent_name,
    agentRole: actingRole,
    toolCalls: action.tool_calls,
    parkedApprovalId: action.parked
      ? (action.approval_id ?? undefined)
      : undefined,
  }
}
