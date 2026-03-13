import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import SpendingSummary from '@/components/dashboard/SpendingSummary.vue'
import type { CostRecord } from '@/api/types'

vi.mock('vue-echarts', () => ({
  default: { template: '<div class="vchart-stub" />' },
}))

const mockRecord: CostRecord = {
  agent_id: 'agent-1',
  task_id: 't1',
  model: 'test-model',
  provider: 'test-provider',
  input_tokens: 100,
  output_tokens: 50,
  cost_usd: 0.005,
  timestamp: '2026-03-12T10:00:00Z',
  call_category: null,
}

describe('SpendingSummary', () => {
  it('renders "Spending" heading', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [], totalCost: 0 },
    })
    expect(wrapper.text()).toContain('Spending')
  })

  it('shows total cost formatted with 4 decimal places', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [mockRecord], totalCost: 1.2345 },
    })
    expect(wrapper.text()).toContain('$1.2345')
  })

  it('shows "$0.0000" when total cost is zero', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [], totalCost: 0 },
    })
    expect(wrapper.text()).toContain('$0.0000')
  })

  it('shows "No spending data yet" when records are empty', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [], totalCost: 0 },
    })
    expect(wrapper.text()).toContain('No spending data yet')
  })

  it('does not show "No spending data yet" when records are present', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [mockRecord], totalCost: 0.005 },
    })
    expect(wrapper.text()).not.toContain('No spending data yet')
  })

  it('renders chart stub when records are present', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [mockRecord], totalCost: 0.005 },
    })
    expect(wrapper.find('.vchart-stub').exists()).toBe(true)
  })

  it('does not render chart when records are empty', () => {
    const wrapper = mount(SpendingSummary, {
      props: { records: [], totalCost: 0 },
    })
    expect(wrapper.find('.vchart-stub').exists()).toBe(false)
  })

  it('displays correct total for multiple records', () => {
    const records: CostRecord[] = [
      { ...mockRecord, cost_usd: 0.005 },
      { ...mockRecord, agent_id: 'agent-2', cost_usd: 0.010 },
    ]
    const wrapper = mount(SpendingSummary, {
      props: { records, totalCost: 0.015 },
    })
    expect(wrapper.text()).toContain('$0.0150')
  })
})
