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
  name: 'alice',
  role: 'Developer',
  seniority: 'senior',
  department: 'engineering',
  team: 'backend',
  status: 'active',
  model: 'example-large-001',
  personality: {
    risk_tolerance: 'medium',
    creativity_level: 'high',
    decision_making_style: 'analytical',
    collaboration_preference: 'team',
    conflict_approach: 'collaborate',
  },
  tools: ['file_system', 'git'],
  description: 'Backend developer',
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
    store.agents = [mockAgent]
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
    store.agents = [mockAgent]
    const event: WsEvent = {
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { name: 'alice', status: 'on_leave' },
    }
    store.handleWsEvent(event)
    expect(store.agents[0].status).toBe('on_leave')
  })
})
