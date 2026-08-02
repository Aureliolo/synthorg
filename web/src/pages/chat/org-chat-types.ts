import type { CitedRecord } from '@/api/endpoints/meta'
import type { ExecutedToolCall } from '@/api/types/meta-turn'

/**
 * The rendered transcript model for the one unified org conversation.
 *
 * Every turn kind the operator can see in a single "talk to your org"
 * thread: their own message, a Chief-of-Staff answer, an attributed
 * specialist voice, a system notice, or an inline event card (a drafted
 * plan, a parked approval, an agent action, a group invite, a charter
 * draft). One discriminated union so the transcript renders heterogeneous
 * turns without a mode switch.
 */

/** The operator's own message. */
export interface OrgHumanTurn {
  id: number
  kind: 'human'
  content: string
  timestamp?: string | undefined
  /** Idempotency key minted for the send; a retry replays it. */
  idempotencyKey?: string | undefined
  /** Project snapshot at send time, replayed on retry. */
  project?: string | undefined
}

/** A Chief-of-Staff answer (the EXPLAIN capability); may stream. */
export interface OrgAssistantTurn {
  id: number
  kind: 'assistant'
  content: string
  /** Header label; defaults to "Chief of Staff". The charter voice is "CEO". */
  roleLabel?: string | undefined
  sources?: readonly string[] | undefined
  citedRecords?: readonly CitedRecord[] | undefined
  confidence?: number | undefined
  timestamp?: string | undefined
  /** True while streamed tokens are still arriving. */
  isStreaming?: boolean | undefined
  /** Renders as a distinct error notice with a retry. */
  isError?: boolean | undefined
}

/** An attributed specialist voice (a routed reply or a group contribution). */
export interface OrgAgentTurn {
  id: number
  kind: 'agent'
  content: string
  agentName?: string | undefined
  agentRole?: string | undefined
  agentTopic?: string | null | undefined
  timestamp?: string | undefined
}

/** A system line (truncation, a skipped agent, a failed turn). */
export interface OrgNoticeTurn {
  id: number
  kind: 'notice'
  content: string
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean | undefined
}

/** A plan drafted from the request, awaiting holistic review in Plan Review. */
export interface PlanDraftedEvent {
  type: 'plan-drafted'
  title: string
  project: string
}

/** Parked steering directives, each with its approval id for a deep link. */
export interface SteeringEvent {
  type: 'steering'
  items: readonly { text: string; approvalId: string }[]
}

/** The outcome of a direct agent action (executed tools + optional parking). */
export interface ActionEvent {
  type: 'action'
  agentName?: string | undefined
  agentRole?: string | undefined
  toolCalls?: readonly ExecutedToolCall[] | undefined
  parkedApprovalId?: string | undefined
  content?: string | undefined
}

/** An agent-initiated invite awaiting the operator's consent. */
export interface InviteEvent {
  type: 'invite'
  content: string
  requestedByName?: string | undefined
  targetName?: string | undefined
  targetRole?: string | undefined
  approvalId?: string | undefined
  resolved?: 'approved' | 'declined' | undefined
}

/** A charter draft is ready in the side panel (the CHARTER capability). */
export interface CharterDraftedEvent {
  type: 'charter-drafted'
  charterId: string
}

/** One secret field the operator console asked the operator to provide. */
export interface SecretCaptureField {
  connectionType: string
  fieldName: string
  secretKind: string
  label?: string | undefined
}

/**
 * The operator console needs one or more secret fields captured out of band to
 * finish a setup. The card renders a masked input per field; the raw value
 * posts straight to the capture endpoint and only an opaque handle flows back
 * into the next turn, so the secret never enters the transcript.
 */
export interface SecretCaptureEvent {
  type: 'secret-capture'
  draftId: string
  captures: readonly SecretCaptureField[]
  /** Set once the operator has submitted the captured secrets. */
  resolved?: 'submitted' | undefined
  /**
   * Stable dedup nonce for this card's configure submission, minted on the
   * first attempt and reused on every retry so a lost response cannot drive a
   * duplicate side effect (the server dedups the identical request). Transient
   * client UX state, never persisted across reloads.
   */
  idempotencyKey?: string | undefined
}

/** One option a project decision offers the operator to pick between. */
export interface QuestionOption {
  id: string
  title: string
  summary: string
  recommended: boolean
}

/**
 * A running agent stopped and asked. The card answers it in place, which
 * resumes the run; declining resumes it on the agent's own judgement.
 */
export interface QuestionEvent {
  type: 'question'
  approvalId: string
  question: string
  askedByName: string
  taskTitle?: string | undefined
  project?: string | undefined
  /** The agent declared the choice hard to reverse, so answering matters more. */
  hardToReverse: boolean
  /** Non-empty only for a project decision; the operator picks one. */
  options: readonly QuestionOption[]
  askedAt: string
}

export type OrgEvent =
  | PlanDraftedEvent
  | SteeringEvent
  | ActionEvent
  | InviteEvent
  | CharterDraftedEvent
  | SecretCaptureEvent
  | QuestionEvent

/** An inline event card in the transcript. */
export interface OrgEventTurn {
  id: number
  kind: 'event'
  event: OrgEvent
}

/** One rendered turn in the unified org conversation. */
export type OrgTurn =
  | OrgHumanTurn
  | OrgAssistantTurn
  | OrgAgentTurn
  | OrgNoticeTurn
  | OrgEventTurn
