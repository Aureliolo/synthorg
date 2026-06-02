import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  ActiveAgentSummary,
  ConversationParticipant,
  GroupConverseResult,
} from '@/api/types'
import { useApprovalsStore } from '@/stores/approvals'
import { useMetaStore } from '@/stores/meta'

export interface GroupMessage {
  id: number
  /** ``human`` = the operator's turn, ``agent`` = an attributed
   *  contribution, ``notice`` = a system line (truncation / failure),
   *  ``invite`` = an agent-initiated invite awaiting human consent. */
  kind: 'human' | 'agent' | 'notice' | 'invite'
  /** Bubble body. For ``invite`` bubbles this is the stated reason. */
  content: string
  /** Attributed agent name, on ``agent`` bubbles. */
  agentName?: string
  /** Attributed agent role, on ``agent`` bubbles. */
  role?: string
  /** Inviting agent's name, on ``invite`` bubbles. */
  requestedByName?: string
  /** Invite target's name, on ``invite`` bubbles. */
  targetName?: string
  /** Invite target's role, on ``invite`` bubbles (``undefined`` when the
   *  target was named directly rather than by role). */
  targetRole?: string
  /** Backing approval id, on ``invite`` bubbles: the in-context
   *  Approve/Reject buttons resolve this approval. */
  approvalId?: string
  /** Set once the operator resolves an ``invite`` in context. The
   *  invited agent joins on the next round after ``approved``. */
  resolved?: 'approved' | 'declined'
}

export interface MetaGroupState {
  activeAgents: readonly ActiveAgentSummary[]
  selectedIds: readonly string[]
  started: boolean
  roster: readonly ConversationParticipant[]
  messages: readonly GroupMessage[]
  input: string
  loading: boolean
  /** Approval ids whose in-context Approve/Reject is in flight. */
  resolvingInvites: ReadonlySet<string>
  scrollRef: React.RefObject<HTMLDivElement | null>
  toggleParticipant: (id: string) => void
  setInput: (value: string) => void
  triggerSend: () => void
  /** Resolve an agent-initiated invite in context (approve or decline). */
  resolveInvite: (msgId: number, approvalId: string, accept: boolean) => void
}

const TRUNCATION_NOTICE: Readonly<Record<string, string>> = {
  token_budget_exhausted:
    'Round stopped early: the per-round token budget was exhausted before every agent could respond.',
  max_total_turns_reached:
    'Round stopped early: the conversation reached its total-turn limit.',
}

export function useMetaGroupState(): MetaGroupState {
  const activeAgents = useMetaStore((s) => s.activeAgents)
  const loading = useMetaStore((s) => s.groupChatLoading)
  const converse = useMetaStore((s) => s.converseGroup)
  const fetchActiveAgents = useMetaStore((s) => s.fetchActiveAgents)

  const [selectedIds, setSelectedIds] = useState<readonly string[]>([])
  const [roster, setRoster] = useState<readonly ConversationParticipant[]>([])
  const [messages, setMessages] = useState<readonly GroupMessage[]>([])
  const [input, setInput] = useState('')
  const [started, setStarted] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const msgIdRef = useRef(0)
  const conversationIdRef = useRef<string | undefined>(undefined)

  const fetchRef = useRef(fetchActiveAgents)
  fetchRef.current = fetchActiveAgents
  useEffect(() => {
    void fetchRef.current()
  }, [])

  const nextMsgId = useCallback(() => ++msgIdRef.current, [])

  const toggleParticipant = useCallback((id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }, [])

  const handleSend = useCallback(async () => {
    const message = input.trim()
    const canStart = conversationIdRef.current !== undefined || selectedIds.length > 0
    if (!message || loading || !canStart) return
    setInput('')
    setMessages((prev) => [
      ...prev,
      { id: nextMsgId(), kind: 'human', content: message },
    ])
    const result = await converse(message, selectedIds, conversationIdRef.current)
    setMessages((prev) => [...prev, ...buildRoundMessages(result, nextMsgId)])
    if (result) {
      conversationIdRef.current = result.conversation_id
      setRoster(result.participants)
      setStarted(true)
    }
    scrollToBottom(scrollRef)
  }, [input, loading, selectedIds, converse, nextMsgId])

  const triggerSend = useCallback(() => void handleSend(), [handleSend])

  const { resolvingInvites, resolveInvite } = useInviteResolution(setMessages)

  return {
    activeAgents,
    selectedIds,
    started,
    roster,
    messages,
    input,
    loading,
    resolvingInvites,
    scrollRef,
    toggleParticipant,
    setInput,
    triggerSend,
    resolveInvite,
  }
}

function useInviteResolution(
  setMessages: React.Dispatch<React.SetStateAction<readonly GroupMessage[]>>,
): {
  resolvingInvites: ReadonlySet<string>
  resolveInvite: (msgId: number, approvalId: string, accept: boolean) => void
} {
  const [resolvingInvites, setResolvingInvites] = useState<ReadonlySet<string>>(
    () => new Set(),
  )

  const handleResolveInvite = useCallback(
    async (msgId: number, approvalId: string, accept: boolean) => {
      // approveOne / rejectOne own their error + success toast UX and
      // never throw (they return null on failure), so no try/catch here.
      setResolvingInvites((prev) => new Set(prev).add(approvalId))
      const store = useApprovalsStore.getState()
      const result = accept
        ? await store.approveOne(approvalId)
        : await store.rejectOne(approvalId, {
            reason: 'Declined from group chat',
          })
      setResolvingInvites((prev) => {
        const next = new Set(prev)
        next.delete(approvalId)
        return next
      })
      if (result) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msgId
              ? { ...m, resolved: accept ? 'approved' : 'declined' }
              : m,
          ),
        )
      }
    },
    [setMessages],
  )

  const resolveInvite = useCallback(
    (msgId: number, approvalId: string, accept: boolean) =>
      void handleResolveInvite(msgId, approvalId, accept),
    [handleResolveInvite],
  )

  return { resolvingInvites, resolveInvite }
}

function buildRoundMessages(
  result: GroupConverseResult | null,
  nextMsgId: () => number,
): GroupMessage[] {
  if (!result) {
    const errMsg = useMetaStore.getState().error
    return [
      {
        id: nextMsgId(),
        kind: 'notice',
        content: errMsg
          ? `Group chat request failed: ${errMsg}`
          : 'Failed to get a response. Please try again.',
      },
    ]
  }
  const bubbles: GroupMessage[] = result.contributions.map((c) => ({
    id: nextMsgId(),
    kind: 'agent',
    content: c.content,
    agentName: c.agent_name,
    role: c.participant_role,
  }))
  if (result.truncated_reason) {
    bubbles.push({
      id: nextMsgId(),
      kind: 'notice',
      content:
        TRUNCATION_NOTICE[result.truncated_reason] ?? 'Round stopped early.',
    })
  }
  for (const invite of result.pending_invites) {
    bubbles.push({
      id: nextMsgId(),
      kind: 'invite',
      content: invite.reason,
      requestedByName: invite.requested_by_name,
      targetName: invite.target_name,
      targetRole: invite.target_role ?? undefined,
      approvalId: invite.approval_id,
    })
  }
  return bubbles
}

function scrollToBottom(scrollRef: React.RefObject<HTMLDivElement | null>): void {
  requestAnimationFrame(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  })
}
