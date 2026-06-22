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
  agentName?: string | undefined
  /** Tools the action executed, on ``action`` bubbles. */
  toolCalls?: readonly ExecutedToolCall[] | undefined
  /** Approval id, on ``action`` bubbles when the action parked for consent. */
  parkedApprovalId?: string | undefined
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean
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
  retryLast: () => void
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

  const sendInstruction = useCallback(
    async (instruction: string) => {
      if (!instruction || loading || !selectedAgentId) return
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
    },
    [loading, selectedAgentId, runAction, nextMsgId],
  )

  // ``runAction`` owns its error UX (catches internally, returns ``null`` on
  // failure), so voiding the promise is safe -- there is no rejection to leak.
  const triggerSend = useCallback(() => {
    const instruction = input.trim()
    if (!instruction) return
    setInput('')
    void sendInstruction(instruction)
  }, [input, sendInstruction])

  const retryLast = useCallback(() => {
    const lastHuman = [...messages].reverse().find((m) => m.kind === 'human')
    if (lastHuman) void sendInstruction(lastHuman.content)
  }, [messages, sendInstruction])

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

function buildActMessage(
  result: ConversationalActResult | null,
  nextMsgId: () => number,
): ActMessage {
  if (!result) {
    return {
      id: nextMsgId(),
      kind: 'notice',
      content: 'The action could not be completed. Please try again.',
      isError: true,
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
