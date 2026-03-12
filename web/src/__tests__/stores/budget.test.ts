import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBudgetStore } from '@/stores/budget'
import type { CostRecord, WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/budget', () => ({
  getBudgetConfig: vi.fn(),
  listCostRecords: vi.fn(),
  getAgentSpending: vi.fn(),
}))

const mockRecord: CostRecord = {
  agent_id: 'alice',
  task_id: 'task-1',
  provider: 'test-provider',
  model: 'example-large-001',
  input_tokens: 100,
  output_tokens: 50,
  cost_usd: 0.005,
  timestamp: '2026-03-12T10:00:00Z',
  call_category: null,
}

describe('useBudgetStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initializes with empty state', () => {
    const store = useBudgetStore()
    expect(store.config).toBeNull()
    expect(store.records).toEqual([])
    expect(store.totalRecords).toBe(0)
  })

  it('handles budget.record_added WS event', () => {
    const store = useBudgetStore()
    const event: WsEvent = {
      event_type: 'budget.record_added',
      channel: 'budget',
      timestamp: '2026-03-12T10:00:00Z',
      payload: { ...mockRecord },
    }
    store.handleWsEvent(event)
    expect(store.records).toHaveLength(1)
    expect(store.records[0].cost_usd).toBe(0.005)
  })
})
