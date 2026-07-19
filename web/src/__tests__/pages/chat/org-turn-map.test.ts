import { describe, expect, it } from 'vitest'

import type {
  ConversationalActResult,
  GroupConverseResult,
  InterviewTurnResult,
  ProposeResult,
  TurnResult,
} from '@/api/types'
import { buildCharter } from '@/mocks/handlers'
import { intentDegradeNotice, mapTurnResult } from '@/pages/chat/org-turn-map'

function baseResult(overrides: Partial<TurnResult>): TurnResult {
  return {
    intent: 'explain',
    intent_reason: 'no_intent_classifier',
    intent_confidence: null,
    conversation_id: null,
    answer: null,
    propose: null,
    group: null,
    act: null,
    charter: null,
    chime_ins: [],
    ...overrides,
  }
}

describe('mapTurnResult', () => {
  it('maps an explain answer to a single assistant turn', () => {
    const turns = mapTurnResult(
      baseResult({
        intent: 'explain',
        answer: {
          answer: 'All good.',
          sources: ['performance'],
          cited_records: [],
          confidence: 0.9,
        },
      }),
    )
    expect(turns).toHaveLength(1)
    expect(turns[0]).toMatchObject({ kind: 'assistant', content: 'All good.' })
  })

  it('renders specialist chime-ins as agent turns after the answer', () => {
    const turns = mapTurnResult(
      baseResult({
        intent: 'explain',
        answer: {
          answer: 'Runway is fine.',
          sources: [],
          cited_records: [],
          confidence: 0.9,
        },
        chime_ins: [
          { role: 'CFO', name: 'Casey', content: 'Watch the Q3 renewal, though.' },
        ],
      }),
    )
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ kind: 'assistant' })
    expect(turns[1]).toMatchObject({
      kind: 'agent',
      agentName: 'Casey',
      agentRole: 'CFO',
      content: 'Watch the Q3 renewal, though.',
    })
  })

  it('maps a clarify propose to a plain assistant turn', () => {
    const propose: ProposeResult = {
      conversation_id: 'c1',
      status: 'needs_clarification',
      clarifying_question: 'Which platform?',
      conversation_closed: false,
      plan_draft: null,
      responder_role: null,
      responder_name: null,
      routed_topic: null,
      routing_confidence: null,
      routing_reason: 'no_role_router',
      steering: [],
    }
    const turns = mapTurnResult(baseResult({ intent: 'propose', propose }))
    expect(turns).toHaveLength(1)
    expect(turns[0]).toMatchObject({
      kind: 'assistant',
      content: 'Which platform?',
    })
  })

  it('attributes a routed clarify reply to the answering specialist', () => {
    const propose: ProposeResult = {
      conversation_id: 'c1',
      status: 'needs_clarification',
      clarifying_question: 'What budget?',
      conversation_closed: false,
      plan_draft: null,
      responder_role: 'CFO',
      responder_name: 'Casey',
      routed_topic: 'finance',
      routing_confidence: 0.8,
      routing_reason: 'routed',
      steering: [],
    }
    const turns = mapTurnResult(baseResult({ intent: 'propose', propose }))
    expect(turns[0]).toMatchObject({
      kind: 'agent',
      agentName: 'Casey',
      agentRole: 'CFO',
      agentTopic: 'finance',
    })
  })

  it('maps a proposed plan to a reply plus a plan-drafted event', () => {
    const propose: ProposeResult = {
      conversation_id: 'c1',
      status: 'proposed',
      clarifying_question: null,
      conversation_closed: false,
      plan_draft: { task_id: 't1', project: 'Growth', title: 'Launch' },
      responder_role: null,
      responder_name: null,
      routed_topic: null,
      routing_confidence: null,
      routing_reason: 'no_role_router',
      steering: [],
    }
    const turns = mapTurnResult(baseResult({ intent: 'propose', propose }))
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ content: 'Drafted a plan for your review.' })
    expect(turns[1]).toMatchObject({
      kind: 'event',
      event: { type: 'plan-drafted', title: 'Launch', project: 'Growth' },
    })
  })

  it('maps steering directives to a steering event', () => {
    const propose: ProposeResult = {
      conversation_id: 'c1',
      status: 'proposed',
      clarifying_question: null,
      conversation_closed: false,
      plan_draft: null,
      responder_role: null,
      responder_name: null,
      routed_topic: null,
      routing_confidence: null,
      routing_reason: 'no_role_router',
      steering: [
        { text: 'Pause hiring', approval_id: 'a1', kind: 'hint', project: 'P' },
      ],
    }
    const turns = mapTurnResult(baseResult({ intent: 'propose', propose }))
    expect(turns[0]).toMatchObject({
      content: 'Queued 1 steering directive for your confirmation.',
    })
    expect(turns[1]).toMatchObject({
      kind: 'event',
      event: { type: 'steering', items: [{ text: 'Pause hiring', approvalId: 'a1' }] },
    })
  })

  it('maps group contributions to agent turns, truncation and invites to cards', () => {
    const group: GroupConverseResult = {
      conversation_id: 'g1',
      contributions: [
        {
          agent_id: 'a-ceo',
          agent_name: 'Dana',
          participant_role: 'CEO',
          content: 'Prioritise enterprise.',
          sequence: 1,
          input_tokens: 10,
          output_tokens: 5,
        },
      ],
      participants: [],
      participants_skipped: [],
      truncated_reason: 'token_budget_exhausted',
      pending_invites: [
        {
          approval_id: 'inv-1',
          reason: 'need finance view',
          requested_by_agent_id: 'a-ceo',
          requested_by_name: 'Dana',
          target_agent_id: 'a-cfo',
          target_name: 'Casey',
          target_role: 'CFO',
        },
      ],
    }
    const turns = mapTurnResult(baseResult({ intent: 'group_convene', group }))
    expect(turns[0]).toMatchObject({ kind: 'agent', agentName: 'Dana' })
    expect(turns[1]).toMatchObject({ kind: 'notice' })
    expect(turns[2]).toMatchObject({
      kind: 'event',
      event: { type: 'invite', targetName: 'Casey', approvalId: 'inv-1' },
    })
  })

  it('maps an act result to an action event with tool calls', () => {
    const act: ConversationalActResult = {
      agent_id: 'a-cfo',
      agent_name: 'Casey',
      conversation_id: 'c1',
      action: {
        termination_reason: 'completed',
        final_message: 'Done.',
        tool_calls: [{ tool_name: 'query_metrics', is_error: false, result: 'ok' }],
        approval_id: null,
        parked: false,
      },
    }
    const turns = mapTurnResult(baseResult({ intent: 'act', act }))
    expect(turns).toHaveLength(1)
    expect(turns[0]).toMatchObject({
      kind: 'event',
      event: {
        type: 'action',
        agentName: 'Casey',
        content: 'Done.',
        // The tool calls must survive the mapping, or a regression that drops
        // or corrupts them would still pass the type/agent/content checks.
        toolCalls: [{ tool_name: 'query_metrics', is_error: false, result: 'ok' }],
      },
    })
  })

  it('maps a charter question to a CEO-labelled assistant turn', () => {
    const charter: InterviewTurnResult = {
      charter: null,
      conversation_closed: false,
      conversation_id: 'c1',
      next_question: 'Who is the customer?',
      status: 'needs_more',
    }
    const turns = mapTurnResult(baseResult({ intent: 'charter', charter }))
    expect(turns[0]).toMatchObject({
      kind: 'assistant',
      roleLabel: 'CEO',
      content: 'Who is the customer?',
    })
  })

  it('maps a drafted charter to a note plus a charter-drafted event', () => {
    const charter: InterviewTurnResult = {
      charter: buildCharter({ id: 'ch-1' }),
      conversation_closed: false,
      conversation_id: 'c1',
      next_question: null,
      status: 'drafted',
    }
    const turns = mapTurnResult(baseResult({ intent: 'charter', charter }))
    expect(turns).toHaveLength(2)
    expect(turns[1]).toMatchObject({
      kind: 'event',
      event: { type: 'charter-drafted', charterId: 'ch-1' },
    })
  })

  it('appends a notice when an explain answer is a silent intent degrade', () => {
    const turns = mapTurnResult(
      baseResult({
        intent: 'explain',
        intent_reason: 'act_no_target',
        answer: { answer: "Here's what I'd do.", sources: [], cited_records: [], confidence: 0.6 },
      }),
    )
    expect(turns).toHaveLength(2)
    expect(turns[0]).toMatchObject({ kind: 'assistant' })
    expect(turns[1]).toMatchObject({ kind: 'notice' })
    expect((turns[1] as { content: string }).content).toContain('Answered as a question')
  })
})

describe('intentDegradeNotice', () => {
  it('is null for a normally-classified explain answer', () => {
    expect(intentDegradeNotice(baseResult({ intent: 'explain', intent_reason: 'classified' }))).toBeNull()
  })

  it('is null for an explicit override', () => {
    expect(
      intentDegradeNotice(baseResult({ intent: 'explain', intent_reason: 'explicit_override' })),
    ).toBeNull()
  })

  it('is null for a non-explain intent', () => {
    expect(intentDegradeNotice(baseResult({ intent: 'propose', intent_reason: 'classified' }))).toBeNull()
  })

  it('returns a notice for a below-floor act', () => {
    const notice = intentDegradeNotice(
      baseResult({ intent: 'explain', intent_reason: 'act_floor_not_met' }),
    )
    expect(notice).toMatchObject({ kind: 'notice' })
  })

  it('returns a notice for a group-targets-missing degrade', () => {
    const notice = intentDegradeNotice(
      baseResult({ intent: 'explain', intent_reason: 'group_targets_missing' }),
    )
    expect(notice?.content).toContain('convening a group')
  })
})
