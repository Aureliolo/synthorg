import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAgentStore } from '@/stores/agents'
import type { AgentConfig, WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/agents', () => ({
  listAgents: vi.fn(),
  getAgent: vi.fn(),
  getAutonomy: vi.fn(),
  setAutonomy: vi.fn(),
}))

const mockAgent: AgentConfig = {
  id: 'test-uuid-001',
  name: 'alice',
  role: 'Developer',
  level: 'senior',
  department: 'engineering',
  status: 'active',
  model: {
    provider: 'test-provider',
    model_id: 'example-large-001',
    temperature: 0.7,
    max_tokens: 4096,
    fallback_model: null,
  },
  personality: {
    traits: [],
    communication_style: 'neutral',
    risk_tolerance: 'medium',
    creativity: 'high',
    description: '',
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stress_response: 0.5,
    decision_making: 'analytical',
    collaboration: 'team',
    verbosity: 'balanced',
    conflict_approach: 'collaborate',
  },
  skills: { primary: ['python'], secondary: ['go'] },
  memory: { type: 'session', retention_days: null },
  tools: { access_level: 'standard', allowed: ['file_system', 'git'], denied: [] },
  autonomy_level: null,
  hiring_date: '2026-03-01',
}

describe('useAgentStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initializes with empty state', () => {
    const store = useAgentStore()
    expect(store.agents).toEqual([])
    expect(store.total).toBe(0)
  })

  it('handles agent.hired WS event', () => {
    const store = useAgentStore()
    const event: WsEvent = {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-12T10:00:00Z',
      payload: { ...mockAgent },
    }
    store.handleWsEvent(event)
    expect(store.agents).toHaveLength(1)
    expect(store.total).toBe(1)
  })

  it('handles agent.fired WS event', () => {
    const store = useAgentStore()
    store.agents = [{ ...mockAgent }]
    store.total = 1
    const event: WsEvent = {
      event_type: 'agent.fired',
      channel: 'agents',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { name: 'alice' },
    }
    store.handleWsEvent(event)
    expect(store.agents).toHaveLength(0)
    expect(store.total).toBe(0)
  })

  it('handles agent.status_changed WS event', () => {
    const store = useAgentStore()
    store.agents = [{ ...mockAgent }]
    const event: WsEvent = {
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { name: 'alice', status: 'on_leave' },
    }
    store.handleWsEvent(event)
    expect(store.agents[0].status).toBe('on_leave')
  })

  it('does not duplicate agents on repeated agent.hired events', () => {
    const store = useAgentStore()
    store.agents = [{ ...mockAgent }]
    store.total = 1
    const event: WsEvent = {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { ...mockAgent },
    }
    store.handleWsEvent(event)
    expect(store.agents).toHaveLength(1)
    expect(store.total).toBe(1)
  })

  it('ignores agent.hired with malformed payload', () => {
    const store = useAgentStore()
    const event: WsEvent = {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { name: 'bob' }, // missing id, role, department
    }
    store.handleWsEvent(event)
    expect(store.agents).toHaveLength(0)
    expect(store.total).toBe(0)
  })
})
