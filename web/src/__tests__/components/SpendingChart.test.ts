import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { CostRecord } from '@/api/types'

// Mock vue-echarts at the module level to prevent ECharts renderer initialization.
// The factory must be self-contained (no references to outer variables) because vi.mock is hoisted.
vi.mock('vue-echarts', async () => {
  const { defineComponent: dc } = await import('vue')
  return {
    default: dc({
      name: 'VChart',
      props: ['option', 'autoresize'],
      template: '<div class="v-chart" />',
    }),
  }
})

import SpendingChart from '@/components/budget/SpendingChart.vue'

function createRecord(overrides: Partial<CostRecord> = {}): CostRecord {
  return {
    agent_id: 'agent-1',
    task_id: 't1',
    model: 'm1',
    provider: 'p1',
    input_tokens: 100,
    output_tokens: 50,
    cost_usd: 0.005,
    timestamp: '2026-03-12T10:00:00Z',
    call_category: null,
    ...overrides,
  }
}

function mountChart(records: CostRecord[]) {
  return mount(SpendingChart, {
    props: { records },
  })
}

describe('SpendingChart', () => {
  it('renders chart when records are provided', () => {
    const wrapper = mountChart([createRecord()])

    const chart = wrapper.findComponent({ name: 'VChart' })
    expect(chart.exists()).toBe(true)
  })

  it('shows empty state when no records', () => {
    const wrapper = mountChart([])

    const chart = wrapper.findComponent({ name: 'VChart' })
    expect(chart.exists()).toBe(false)

    expect(wrapper.text()).toContain('No spending data available')
  })

  it('passes chart option with aggregated daily data to VChart', () => {
    const records = [
      createRecord({ cost_usd: 0.01, timestamp: '2026-03-10T08:00:00Z' }),
      createRecord({ cost_usd: 0.02, timestamp: '2026-03-10T14:00:00Z' }),
      createRecord({ cost_usd: 0.05, timestamp: '2026-03-11T09:00:00Z' }),
    ]

    const wrapper = mountChart(records)

    const chart = wrapper.findComponent({ name: 'VChart' })
    const option = chart.props('option') as {
      xAxis: { data: string[] }
      series: Array<{ data: number[] }>
    }

    // Two days of data, sorted chronologically
    expect(option.xAxis.data).toEqual(['2026-03-10', '2026-03-11'])
    // First day: 0.01 + 0.02 = 0.03, second day: 0.05
    expect(option.series[0].data[0]).toBeCloseTo(0.03, 6)
    expect(option.series[0].data[1]).toBeCloseTo(0.05, 6)
  })

  it('sorts daily data chronologically', () => {
    const records = [
      createRecord({ cost_usd: 0.03, timestamp: '2026-03-12T10:00:00Z' }),
      createRecord({ cost_usd: 0.01, timestamp: '2026-03-10T10:00:00Z' }),
      createRecord({ cost_usd: 0.02, timestamp: '2026-03-11T10:00:00Z' }),
    ]

    const wrapper = mountChart(records)

    const chart = wrapper.findComponent({ name: 'VChart' })
    const option = chart.props('option') as {
      xAxis: { data: string[] }
    }

    expect(option.xAxis.data).toEqual(['2026-03-10', '2026-03-11', '2026-03-12'])
  })

  it('uses bar chart type', () => {
    const wrapper = mountChart([createRecord()])

    const chart = wrapper.findComponent({ name: 'VChart' })
    const option = chart.props('option') as {
      series: Array<{ type: string }>
    }

    expect(option.series[0].type).toBe('bar')
  })
})
