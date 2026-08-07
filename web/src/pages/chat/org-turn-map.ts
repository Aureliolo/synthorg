import { parseCitedRecords } from '@/api/endpoints/cited-records'
import type {
  ConsoleTurnResult,
  ConversationalActResult,
  GroupChatTruncationReason,
  GroupConverseResult,
  InterviewTurnResult,
  ProposeResult,
  TurnResult,
} from '@/api/types/meta-turn'

import { hasAttribution } from './attribution'
import { nextMessageId } from './message-id'
import type {
  OrgAgentTurn,
  OrgAssistantTurn,
  OrgNoticeTurn,
  OrgTurn,
} from './org-chat-types'

/**
 * Translate a buffered {@link TurnResult} into transcript turns.
 *
 * The one place the wire payloads (one per capability) become renderable
 * bubbles + inline event cards. Pure: no store writes, no side effects, so
 * the streaming path and the buffered path can share it.
 */

// Keyed by the generated enum so a new (or renamed) truncation reason fails the
// build here until it gets a copy line, rather than silently disappearing.
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

/** Attribute a reply to a routed specialist, or fall back to the CoS voice. */
function attributedReply(
  content: string,
  name: string | null,
  role: string | null,
  topic: string | null,
): OrgAgentTurn | OrgAssistantTurn {
  if (hasAttribution(name ?? undefined, role ?? undefined)) {
    return {
      id: nextMessageId(),
      kind: 'agent',
      content,
      agentName: name ?? undefined,
      agentRole: role ?? undefined,
      agentTopic: topic,
    }
  }
  return { id: nextMessageId(), kind: 'assistant', content }
}

function mapExplain(
  answer: TurnResult['answer'],
  chimeIns: TurnResult['chime_ins'],
): OrgTurn[] {
  if (!answer) return []
  const turns: OrgTurn[] = [
    {
      id: nextMessageId(),
      kind: 'assistant',
      content: answer.answer,
      sources: answer.sources,
      citedRecords: parseCitedRecords(answer.cited_records),
      confidence: answer.confidence,
    },
  ]
  // Specialists who cleared the value bar add a short attributed chime-in
  // after the answer, so the operator sees the org's other voices inline.
  for (const chime of chimeIns) {
    turns.push({
      id: nextMessageId(),
      kind: 'agent',
      content: chime.content,
      agentName: chime.name,
      agentRole: chime.role,
    })
  }
  return turns
}

function mapPropose(propose: ProposeResult | null): OrgTurn[] {
  if (!propose) return []
  if (propose.status === 'needs_clarification') {
    const question = propose.clarifying_question ?? 'Could you clarify?'
    return [
      attributedReply(
        question,
        propose.responder_name,
        propose.responder_role,
        propose.routed_topic,
      ),
    ]
  }
  const turns: OrgTurn[] = [
    attributedReply(
      proposedContent(propose),
      propose.responder_name,
      propose.responder_role,
      propose.routed_topic,
    ),
  ]
  if (propose.plan_draft) {
    turns.push({
      id: nextMessageId(),
      kind: 'event',
      event: {
        type: 'plan-drafted',
        title: propose.plan_draft.title,
        project: propose.plan_draft.project,
        reusedProject: propose.plan_draft.reused_project,
      },
    })
  }
  if (propose.steering.length > 0) {
    turns.push({
      id: nextMessageId(),
      kind: 'event',
      event: {
        type: 'steering',
        items: propose.steering.map((s) => ({
          text: s.text,
          approvalId: s.approval_id,
        })),
      },
    })
  }
  return turns
}

// The request yields ONE plan drafted for holistic review (never per-item
// approvals); steering directives, when present, are confirmed in Approvals.
function proposedContent(propose: ProposeResult): string {
  if (propose.plan_draft) return 'Drafted a plan for your review.'
  const count = propose.steering.length
  const plural = count === 1 ? '' : 's'
  return `Queued ${count} steering directive${plural} for your confirmation.`
}

function mapGroup(group: GroupConverseResult | null): OrgTurn[] {
  if (!group) return []
  const turns: OrgTurn[] = group.contributions.map((c) => ({
    id: nextMessageId(),
    kind: 'agent',
    content: c.content,
    agentName: c.agent_name,
    agentRole: c.participant_role,
  }))
  if (group.truncated_reason) {
    const notice: string | undefined = (
      TRUNCATION_NOTICE as Record<string, string | undefined>
    )[group.truncated_reason]
    turns.push({
      id: nextMessageId(),
      kind: 'notice',
      content: notice ?? TRUNCATION_FALLBACK,
    })
  }
  if (group.participants_skipped.length > 0) {
    const nameById = new Map(
      group.participants.map((p) => [p.agent_id, p.agent_name]),
    )
    const names = group.participants_skipped.map((id) => nameById.get(id) ?? id)
    turns.push({
      id: nextMessageId(),
      kind: 'notice',
      content: `${names.join(', ')} did not respond this round.`,
    })
  }
  for (const invite of group.pending_invites) {
    turns.push({
      id: nextMessageId(),
      kind: 'event',
      event: {
        type: 'invite',
        content: invite.reason,
        requestedByName: invite.requested_by_name,
        targetName: invite.target_name,
        targetRole: invite.target_role ?? undefined,
        approvalId: invite.approval_id,
      },
    })
  }
  return turns
}

/** Build the action-event card shared by the act and configure mappings. */
function actionEventTurn(
  agentName: string,
  action: ConsoleTurnResult['action'],
): OrgTurn {
  return {
    id: nextMessageId(),
    kind: 'event',
    event: {
      type: 'action',
      agentName,
      toolCalls: action.tool_calls,
      ...(action.final_message != null && { content: action.final_message }),
      ...(action.parked &&
        action.approval_id != null && {
          parkedApprovalId: action.approval_id,
        }),
    },
  }
}

function mapConfigure(configure: ConsoleTurnResult | null): OrgTurn[] {
  if (!configure) return []
  // The operator console reuses the same governed action loop as a direct act,
  // so it renders as an action event attributed to the console identity.
  const turns: OrgTurn[] = [
    actionEventTurn(configure.console_name, configure.action),
  ]
  // The console asked for one or more secrets out of band: render a masked
  // capture card so the operator provides them without the value ever entering
  // the transcript. Only opaque handles flow back on the next turn.
  if (configure.pending_captures.length > 0 && configure.connection_draft_id) {
    turns.push({
      id: nextMessageId(),
      kind: 'event',
      event: {
        type: 'secret-capture',
        draftId: configure.connection_draft_id,
        captures: configure.pending_captures.map((c) => ({
          connectionType: c.connection_type,
          fieldName: c.field_name,
          secretKind: c.secret_kind,
          ...(c.label != null && { label: c.label }),
        })),
      },
    })
  }
  return turns
}

function mapAct(act: ConversationalActResult | null): OrgTurn[] {
  if (!act) return []
  return [actionEventTurn(act.agent_name, act.action)]
}

function mapCharter(charter: InterviewTurnResult | null): OrgTurn[] {
  if (!charter) return []
  const turns: OrgTurn[] = []
  if (charter.status === 'needs_more') {
    turns.push({
      id: nextMessageId(),
      kind: 'assistant',
      roleLabel: 'CEO',
      content: charter.next_question ?? 'Tell me more about your idea.',
    })
    return turns
  }
  turns.push({
    id: nextMessageId(),
    kind: 'assistant',
    roleLabel: 'CEO',
    content:
      'Charter drafted. Review and edit it in the panel, then approve to start the run.',
  })
  if (charter.charter) {
    turns.push({
      id: nextMessageId(),
      kind: 'event',
      event: { type: 'charter-drafted', charterId: charter.charter.id },
    })
  }
  return turns
}

// Why a turn that meant to act / convene / charter was answered as a plain
// question instead. The classifier ships the reason so the operator is never
// silently downgraded; a `null` entry (classified / explicit override / a
// fixed-kind thread) is a normal answer and shows no notice.
const DEGRADE_NOTICE: Record<TurnResult['intent_reason'], string | null> = {
  classified: null,
  explicit_override: null,
  conversation_kind_fixed: null,
  no_intent_classifier: null,
  act_floor_not_met:
    'Answered as a question: I was not confident enough you wanted an action taken. Give a clear, specific instruction to act.',
  act_no_target:
    'Answered as a question: no acting agent was named. Say who should act (e.g. "have finance…").',
  charter_floor_not_met:
    'Answered as a question rather than starting a company charter. Say explicitly you want to define a new company.',
  configure_floor_not_met:
    'Answered as a question rather than configuring the platform. Say explicitly what you want to set up or change (e.g. "connect our issue tracker").',
  group_targets_missing:
    'Answered as a question rather than convening a group. Name the roles you want in the room.',
  classify_call_failed:
    'Answered as a question: I could not classify your request just now. Try rephrasing.',
  response_invalid:
    'Answered as a question: I could not classify your request just now. Try rephrasing.',
}

/**
 * A notice explaining a silent intent degrade, or `null` for a normal answer.
 *
 * Surfaced after a degraded EXPLAIN answer so an "act"/"convene"/"charter"
 * request that fell below its confidence floor is visibly distinct from a
 * question the operator actually asked.
 */
export function intentDegradeNotice(result: TurnResult): OrgNoticeTurn | null {
  if (result.intent !== 'explain') return null
  const message = DEGRADE_NOTICE[result.intent_reason]
  if (message === null) return null
  return { id: nextMessageId(), kind: 'notice', content: message }
}

/** Map a resolved turn to its transcript bubbles + inline event cards. */
export function mapTurnResult(result: TurnResult): OrgTurn[] {
  switch (result.intent) {
    case 'propose':
      return mapPropose(result.propose)
    case 'group_convene':
      return mapGroup(result.group)
    case 'act':
      return mapAct(result.act)
    case 'configure':
      return mapConfigure(result.configure)
    case 'charter':
      return mapCharter(result.charter)
    case 'explain': {
      const turns = mapExplain(result.answer, result.chime_ins)
      const notice = intentDegradeNotice(result)
      return notice ? [...turns, notice] : turns
    }
  }
}
