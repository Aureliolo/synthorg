import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBudgetStore } from '@/stores/budget'
import type { BudgetConfig, CostRecord, AgentSpending, WsEvent } from '@/api/types'

const mockGetBudgetConfig = vi.fn()
const mockListCostRecords = vi.fn()
const mockGetAgentSpending = vi.fn()

vi.mock('@/api/endpoints/budget', () => ({
  getBudgetConfig: (...args: unknown[]) => mockGetBudgetConfig(...args),
  listCostRecords: (...args: unknown[]) => mockListCostRecords(...args),
  getAgentSpending: (...args: unknown[]) => mockGetAgentSpending(...args),
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
    vi.clearAllMocks()
  })

  it('initializes with empty state', () => {
    const store = useBudgetStore()
    expect(store.config).toBeNull()
    expect(store.records).toEqual([])
    expect(store.totalRecords).toBe(0)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  describe('fetchConfig', () => {
    it('sets config on success', async () => {
      const mockConfig: BudgetConfig = {
        total_monthly: 1000,
        alerts: { warn_at: 0.8, critical_at: 0.95, hard_stop_at: 1.0 },
        per_task_limit: 10,
        per_agent_daily_limit: 100,
        auto_downgrade: { enabled: false, threshold: 0.9, downgrade_map: [], boundary: 'task_assignment' },
        reset_day: 1,
      }
      mockGetBudgetConfig.mockResolvedValue(mockConfig)

      const store = useBudgetStore()
      await store.fetchConfig()

      expect(store.config).toEqual(mockConfig)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('sets error on failure', async () => {
      mockGetBudgetConfig.mockRejectedValue(new Error('Unauthorized'))

      const store = useBudgetStore()
      await store.fetchConfig()

      expect(store.config).toBeNull()
      expect(store.error).toBe('Unauthorized')
      expect(store.loading).toBe(false)
    })
  })

  describe('fetchRecords', () => {
    it('sets records on success', async () => {
      mockListCostRecords.mockResolvedValue({ data: [mockRecord], total: 1 })

      const store = useBudgetStore()
      await store.fetchRecords()

      expect(store.records).toEqual([mockRecord])
      expect(store.totalRecords).toBe(1)
      expect(store.loading).toBe(false)
    })

    it('sets error on failure', async () => {
      mockListCostRecords.mockRejectedValue(new Error('Server error'))

      const store = useBudgetStore()
      await store.fetchRecords()

      expect(store.records).toEqual([])
      expect(store.error).toBe('Server error')
    })
  })

  describe('fetchAgentSpending', () => {
    it('returns spending on success', async () => {
      const mockSpending: AgentSpending = { agent_id: 'alice', total_cost_usd: 1.5 }
      mockGetAgentSpending.mockResolvedValue(mockSpending)

      const store = useBudgetStore()
      const result = await store.fetchAgentSpending('alice')

      expect(result).toEqual(mockSpending)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('returns null and sets error on failure', async () => {
      mockGetAgentSpending.mockRejectedValue(new Error('Not found'))

      const store = useBudgetStore()
      const result = await store.fetchAgentSpending('alice')

      expect(result).toBeNull()
      expect(store.error).toBe('Not found')
    })
  })

  describe('WS events', () => {
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
      expect(store.totalRecords).toBe(1)
    })

    it('ignores WS event with invalid payload', () => {
      const store = useBudgetStore()
      const event: WsEvent = {
        event_type: 'budget.record_added',
        channel: 'budget',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { not_a_record: true },
      }
      store.handleWsEvent(event)
      expect(store.records).toHaveLength(0)
      expect(store.totalRecords).toBe(0)
    })
  })
})
