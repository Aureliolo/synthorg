import type { CitedRecord } from '@/api/endpoints/meta'
import type { ExecutedToolCall } from '@/api/types'

import type { ChatScopeValue } from './ChatScopePicker'

/**
 * A user turn on a role-tagged surface. Carries the idempotency key minted
 * when it was sent (used by the buffered path; the unscoped streaming
 * Chief-of-Staff path carries no key, so a streamed retry re-runs).
 */
interface UserTurn {
  id: number
  role: 'user'
  content: string
  idempotencyKey?: string | undefined
  /** Scope snapshot at send time, replayed on retry so a reused key never
   *  pairs with a scope the operator changed after the turn failed. */
  scope?: ChatScopeValue | null | undefined
}

/** An assistant reply in the Chief of Staff explain-only transcript. */
interface ChiefOfStaffAssistantMessage {
  id: number
  role: 'assistant'
  content: string
  sources?: string[]
  /** Task / project / approval records the answer is grounded in. */
  citedRecords?: CitedRecord[]
  confidence?: number
  /** True while the reply is still receiving streamed tokens. */
  isStreaming?: boolean
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean
}

/** One rendered turn in the Chief of Staff explain-only transcript. */
export type ChiefOfStaffMessage = UserTurn | ChiefOfStaffAssistantMessage

/** A parked work item, with its approval id for a deep link. */
export interface RequestWorkProposal {
  title: string
  approvalId: string
}

/** A parked steering directive, with its approval id for a deep link. */
export interface RequestWorkSteering {
  text: string
  approvalId: string
}

/** An assistant reply in the clarify-and-propose transcript. */
interface RequestWorkAssistantMessage {
  id: number
  role: 'assistant'
  content: string
  /** Role of the routed agent that answered, when concern-routed. */
  responderRole?: string | undefined
  /** Display name of the routed agent, when concern-routed. */
  responderName?: string | undefined
  /** Concern topic that selected the role, when routed. */
  routedTopic?: string | undefined
  /** Parked work items, on the "proposed" branch. */
  proposals?: readonly RequestWorkProposal[] | undefined
  /** Parked steering directives, on the "proposed" branch. */
  steering?: readonly RequestWorkSteering[] | undefined
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean | undefined
}

/** One rendered turn in the clarify-and-propose transcript. */
export type RequestWorkMessage = UserTurn | RequestWorkAssistantMessage

/** The operator's turn in the multi-agent group transcript. */
interface GroupHumanMessage {
  id: number
  kind: 'human'
  content: string
  idempotencyKey?: string | undefined
  /** Participant snapshot at send time, replayed on a first-round retry so a
   *  reused key never pairs with a roster the operator changed afterwards. */
  participants?: readonly string[] | undefined
}

/** An attributed agent contribution in the group transcript. */
interface GroupAgentMessage {
  id: number
  kind: 'agent'
  content: string
  agentName?: string | undefined
  role?: string | undefined
}

/** A system line (truncation / per-agent failure) in the group transcript. */
interface GroupNoticeMessage {
  id: number
  kind: 'notice'
  content: string
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean
}

/** An agent-initiated invite awaiting human consent. */
interface GroupInviteMessage {
  id: number
  kind: 'invite'
  /** The stated reason for the invite. */
  content: string
  /** Inviting agent's name. */
  requestedByName?: string | undefined
  /** Invite target's name. */
  targetName?: string | undefined
  /** Invite target's role (``undefined`` when named directly, not by role). */
  targetRole?: string | undefined
  /** Backing approval id: the in-context Approve/Reject buttons resolve it. */
  approvalId?: string | undefined
  /** Set once the operator resolves the invite; the agent joins next round. */
  resolved?: 'approved' | 'declined'
}

/** One rendered turn in the multi-agent group transcript. */
export type GroupMessage =
  | GroupHumanMessage
  | GroupAgentMessage
  | GroupNoticeMessage
  | GroupInviteMessage

/** The operator's instruction in the direct-action transcript. */
interface ActHumanMessage {
  id: number
  kind: 'human'
  content: string
  idempotencyKey?: string | undefined
  /** Acting-agent snapshot at send time, replayed on retry so a reused key
   *  never runs the original instruction against a different agent. */
  agentId?: string | undefined
}

/** The agent's outcome (executed tools + message, or a parked approval). */
interface ActActionMessage {
  id: number
  kind: 'action'
  content: string
  /** Acting agent's name. */
  agentName?: string | undefined
  /** Acting agent's role (resolved from the roster). */
  agentRole?: string | undefined
  /** Tools the action executed. */
  toolCalls?: readonly ExecutedToolCall[] | undefined
  /** Approval id, set when the action parked for consent. */
  parkedApprovalId?: string | undefined
}

/** A system line (request failure) in the direct-action transcript. */
interface ActNoticeMessage {
  id: number
  kind: 'notice'
  content: string
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean
}

/** One rendered turn in the direct-action transcript. */
export type ActMessage = ActHumanMessage | ActActionMessage | ActNoticeMessage
