import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  ActiveAgentSummary,
  ConversationalActResult,
  ExecutedToolCall,
} from '@/api/types'
import { useMetaStore } from '@/stores/meta'

export interface ActMessage {
  id: number
  /** ``human`` = the operator's instruction, ``action`` = the agent's
   *  outcome (executed tools + message, or a parked approval),
   *  ``notice`` = a system line (request failure). */
  kind: 'human' | 'action' | 'notice'
  /** Bubble body: the instruction, the agent's final message, or a notice. */
  content: string
  /** Acting agent's name, on ``action`` bubbles. */
  agentName?: string
  /** Tools the action executed, on ``action`` bubbles. */
  toolCalls?: readonly ExecutedToolCall[]
  /** Approval id, on ``action`` bubbles when the action parked for consent. */
  parkedApprovalId?: string
}

export interface MetaActState {
  activeAgents: readonly ActiveAgentSummary[]
  selectedAgentId: string | null
  messages: readonly ActMessage[]
  input: string
  loading: boolean
  scrollRef: React.RefObject<HTMLDivElement | null>
  selectAgent: (id: string) => void
  setInput: (value: string) => void
  triggerSend: () => void
}

export function useMetaActState(): MetaActState {
  const activeAgents = useMetaStore((s) => s.activeAgents)
  const loading = useMetaStore((s) => s.actionLoading)
  const runAction = useMetaStore((s) => s.runAction)
  const fetchActiveAgents = useMetaStore((s) => s.fetchActiveAgents)

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [messages, setMessages] = useState<readonly ActMessage[]>([])
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)
  const conversationIdRef = useRef<string | undefined>(undefined)

  const fetchRef = useRef(fetchActiveAgents)
  fetchRef.current = fetchActiveAgents
  useEffect(() => {
    void fetchRef.current()
  }, [])

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const selectAgent = useCallback((id: string) => {
    setSelectedAgentId((prev) => (prev === id ? null : id))
  }, [])

  const handleSend = useCallback(async () => {
    const instruction = input.trim()
    if (!instruction || loading || !selectedAgentId) return
    setInput('')
    setMessages((prev) => [
      ...prev,
      { id: nextMsgId(), kind: 'human', content: instruction },
    ])
    const result = await runAction(
      instruction,
      selectedAgentId,
      conversationIdRef.current,
    )
    setMessages((prev) => [...prev, buildActMessage(result, nextMsgId)])
    if (result) {
      conversationIdRef.current = result.conversation_id ?? undefined
    }
    scrollToBottom(scrollRef)
  }, [input, loading, selectedAgentId, runAction, nextMsgId])

  // ``handleSend`` cannot reject: its only awaited call is the
  // ``runAction`` store mutation, which owns its error UX (catches
  // internally and returns ``null`` on failure). Voiding the promise is
  // therefore safe -- there is no rejection path to leak.
  const triggerSend = useCallback(() => void handleSend(), [handleSend])

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
  }
}

function buildActMessage(
  result: ConversationalActResult | null,
  nextMsgId: () => number,
): ActMessage {
  if (!result) {
    const errMsg = useMetaStore.getState().error
    return {
      id: nextMsgId(),
      kind: 'notice',
      content: errMsg
        ? `Action request failed: ${errMsg}`
        : 'Failed to perform the action. Please try again.',
    }
  }
  const action = result.action
  return {
    id: nextMsgId(),
    kind: 'action',
    content: action.final_message ?? '',
    agentName: result.agent_name,
    toolCalls: action.tool_calls,
    parkedApprovalId: action.parked
      ? (action.approval_id ?? undefined)
      : undefined,
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
