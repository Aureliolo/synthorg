import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  ActiveAgentSummary,
  ConversationParticipant,
  GroupChatTruncationReason,
  GroupConverseResult,
} from '@/api/types'
import { useApprovalsStore } from '@/stores/approvals'
import { useConversationsStore } from '@/stores/conversations'
import { useMetaStore } from '@/stores/meta'
import type { GroupMessage } from './chat-types'
import { nextMessageId } from './message-id'
import { resolveScopedRetryTarget } from './scoped-retry'
import { useScrollToBottom } from './use-scroll-to-bottom'

export type { GroupMessage } from './chat-types'

export interface GroupChatState {
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
  retryLast: (beforeMsgId?: number) => void
  /** Resolve an agent-initiated invite in context (approve or decline). */
  resolveInvite: (msgId: number, approvalId: string, accept: boolean) => void
}

// Keyed by the generated enum so a new (or renamed) truncation reason fails
// the build here until it gets a copy line, rather than silently falling back.
const TRUNCATION_NOTICE = {
  token_budget_exhausted:
    'Round stopped early: the per-round token budget was exhausted before every agent could respond.',
  max_total_turns_reached:
    'Round stopped early: the conversation reached its total-turn limit.',
  input_budget_exhausted:
    'Round stopped early: the conversation history grew too large to fit the remaining round budget.',
} satisfies Record<GroupChatTruncationReason, string>

const TRUNCATION_FALLBACK =
  'The round stopped early for an unspecified reason. You can start a new round.'

type SetGroup = ReturnType<typeof useConversationsStore.getState>['setGroup']
type ConverseGroup = ReturnType<typeof useMetaStore.getState>['converseGroup']

interface GroupSendDeps {
  converse: ConverseGroup
  setGroup: SetGroup
  messages: readonly GroupMessage[]
  input: string
  setInput: (value: string) => void
}

function useGroupSend(deps: GroupSendDeps): {
  triggerSend: () => void
  retryLast: (beforeMsgId?: number) => void
} {
  const { converse, setGroup, messages, input, setInput } = deps

  const sendMessage = useCallback(
    async (
      message: string,
      idempotencyKey?: string,
      participantsOverride?: readonly string[],
    ) => {
      const group = useConversationsStore.getState().group
      const conversationId = group.conversationId
      // A first-round retry replays the participant set the turn was minted
      // against; a fresh send reads the live selection (not the render-time
      // closure) so a quick selection change before the re-render still opens
      // the group with the roster the operator meant and mints the idempotency
      // key for that exact payload.
      const participants = participantsOverride ?? group.selectedIds
      const canStart = conversationId !== undefined || participants.length > 0
      // Read the live loading flag (not the render-time closure) so a rapid
      // second submit in the same render window can't slip past a turn that
      // is already in flight.
      if (!message || useMetaStore.getState().groupChatLoading || !canStart) return
      // Mint the key once per logical turn; a manual retry reuses it so a
      // round that actually ran server-side is deduped, not re-run.
      const key = idempotencyKey ?? crypto.randomUUID()
      setGroup((s) => ({
        messages: [
          ...s.messages,
          {
            id: nextMessageId(),
            kind: 'human',
            content: message,
            idempotencyKey: key,
            participants,
          },
        ],
      }))
      const result = await converse(message, participants, conversationId, key)
      setGroup((s) => ({ messages: [...s.messages, ...buildRoundMessages(result)] }))
      if (result) {
        setGroup({
          conversationId: result.conversation_id,
          roster: result.participants,
          started: true,
        })
      }
    },
    [converse, setGroup],
  )

  const triggerSend = useCallback(() => {
    // Mirror sendMessage's preconditions before clearing the input, so a send
    // blocked by an in-flight turn or an unstartable conversation does not
    // discard the operator's composed text. Read the selection live for the
    // same reason sendMessage does.
    const group = useConversationsStore.getState().group
    const canStart =
      group.conversationId !== undefined || group.selectedIds.length > 0
    const message = input.trim()
    if (useMetaStore.getState().groupChatLoading || !canStart || !message) return
    setInput('')
    void sendMessage(message)
  }, [input, setInput, sendMessage])

  // Retry the human message that precedes the clicked error bubble (see
  // ``resolveScopedRetryContent``); an unscoped retry would resend the wrong
  // turn when multiple failures exist.
  const retryLast = useCallback(
    (beforeMsgId?: number) => {
      const target = resolveScopedRetryTarget(
        messages,
        beforeMsgId,
        (m) => m.kind === 'human',
      )
      if (target && target.kind === 'human') {
        void sendMessage(target.content, target.idempotencyKey, target.participants)
      }
    },
    [messages, sendMessage],
  )

  return { triggerSend, retryLast }
}

function useInviteResolution(setGroup: SetGroup): {
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
        setGroup((s) => ({
          messages: s.messages.map((m) =>
            m.id === msgId
              ? { ...m, resolved: accept ? 'approved' : 'declined' }
              : m,
          ),
        }))
      }
    },
    [setGroup],
  )

  const resolveInvite = useCallback(
    (msgId: number, approvalId: string, accept: boolean) =>
      void handleResolveInvite(msgId, approvalId, accept),
    [handleResolveInvite],
  )

  return { resolvingInvites, resolveInvite }
}

export function useGroupChatState(): GroupChatState {
  const activeAgents = useMetaStore((s) => s.activeAgents)
  const loading = useMetaStore((s) => s.groupChatLoading)
  const converse = useMetaStore((s) => s.converseGroup)
  const fetchActiveAgents = useMetaStore((s) => s.fetchActiveAgents)

  const messages = useConversationsStore((s) => s.group.messages)
  const selectedIds = useConversationsStore((s) => s.group.selectedIds)
  const roster = useConversationsStore((s) => s.group.roster)
  const started = useConversationsStore((s) => s.group.started)
  const setGroup = useConversationsStore((s) => s.setGroup)
  const [input, setInput] = useState('')
  const scrollRef = useScrollToBottom(messages)

  const fetchRef = useRef(fetchActiveAgents)
  fetchRef.current = fetchActiveAgents
  useEffect(() => {
    void fetchRef.current()
  }, [])

  const toggleParticipant = useCallback(
    (id: string) =>
      setGroup((s) => ({
        selectedIds: s.selectedIds.includes(id)
          ? s.selectedIds.filter((x) => x !== id)
          : [...s.selectedIds, id],
      })),
    [setGroup],
  )

  const { triggerSend, retryLast } = useGroupSend({
    converse,
    setGroup,
    messages,
    input,
    setInput,
  })

  const { resolvingInvites, resolveInvite } = useInviteResolution(setGroup)

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
    retryLast,
    resolveInvite,
  }
}

function buildRoundMessages(result: GroupConverseResult | null): GroupMessage[] {
  if (!result) {
    return [
      {
        id: nextMessageId(),
        kind: 'notice',
        content: 'The group could not respond. Please try again.',
        isError: true,
      },
    ]
  }
  const bubbles: GroupMessage[] = result.contributions.map((c) => ({
    id: nextMessageId(),
    kind: 'agent',
    content: c.content,
    agentName: c.agent_name,
    role: c.participant_role,
  }))
  if (result.truncated_reason) {
    // Widen the lookup so a backend that is briefly ahead of this build (a
    // reason not yet in the map) still degrades to the fallback copy.
    const notice: string | undefined = (
      TRUNCATION_NOTICE as Record<string, string | undefined>
    )[result.truncated_reason]
    bubbles.push({
      id: nextMessageId(),
      kind: 'notice',
      content: notice ?? TRUNCATION_FALLBACK,
    })
  }
  if (result.participants_skipped.length > 0) {
    // A per-agent dispatch failure skips that agent without a truncated_reason;
    // name who stayed silent so a missing contribution is never unexplained.
    const nameById = new Map(
      result.participants.map((p) => [p.agent_id, p.agent_name]),
    )
    const names = result.participants_skipped.map((id) => nameById.get(id) ?? id)
    bubbles.push({
      id: nextMessageId(),
      kind: 'notice',
      content: `${names.join(', ')} did not respond this round.`,
    })
  }
  for (const invite of result.pending_invites) {
    bubbles.push({
      id: nextMessageId(),
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
