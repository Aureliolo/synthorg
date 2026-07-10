import { describe, expect, it } from 'vitest'

import { AguiEventType, parseAguiEvent } from '@/api/sse/agui-types'

describe('parseAguiEvent', () => {
  it('parses a valid tool_call_start event', () => {
    const event = parseAguiEvent({
      id: 'evt-1',
      type: 'tool_call_start',
      session_id: 'task-1',
      agent_id: 'agent-x',
      payload: { turn: 2, tools: ['search', 'read_file'] },
    })
    expect(event).not.toBeNull()
    expect(event?.type).toBe(AguiEventType.ToolCallStart)
    expect(event?.sessionId).toBe('task-1')
    expect(event?.agentId).toBe('agent-x')
    expect(event?.payload['tools']).toEqual(['search', 'read_file'])
  })

  it('returns null for an unknown event type', () => {
    // Valid id + session_id so this exercises unknown-TYPE rejection, not the
    // separate missing-identifier (malformed-frame) rejection.
    expect(
      parseAguiEvent({ id: 'evt-x', type: 'not_a_real_event', session_id: 'task-x', payload: {} }),
    ).toBeNull()
  })

  it('returns null for non-object frames', () => {
    expect(parseAguiEvent('nope')).toBeNull()
    expect(parseAguiEvent(null)).toBeNull()
    expect(parseAguiEvent([1, 2])).toBeNull()
  })

  it('sanitises the tools list, dropping non-strings', () => {
    const event = parseAguiEvent({
      id: 'evt-2',
      type: 'tool_call_start',
      session_id: 'task-2',
      payload: { tools: ['ok', 123, null, 'fine'] },
    })
    expect(event?.payload['tools']).toEqual(['ok', 'fine'])
  })

  it('defaults a missing agent_id to null', () => {
    const event = parseAguiEvent({
      id: 'evt-3',
      type: 'run_started',
      session_id: 'task-3',
      payload: {},
    })
    expect(event?.agentId).toBeNull()
  })

  it('returns null for a frame missing its required id or session_id', () => {
    // A blank-identifier frame is malformed: the progress reducer acts on
    // `type` alone, so a session-less `run_finished` could otherwise falsely
    // terminate progress.
    expect(parseAguiEvent({ type: 'run_started', session_id: 'task-1', payload: {} })).toBeNull()
    expect(parseAguiEvent({ id: 'evt-1', type: 'run_started', payload: {} })).toBeNull()
    expect(
      parseAguiEvent({ id: '  ', type: 'run_started', session_id: 'task-1', payload: {} }),
    ).toBeNull()
  })
})
