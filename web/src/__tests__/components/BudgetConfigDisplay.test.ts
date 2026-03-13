import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BudgetConfigDisplay from '@/components/budget/BudgetConfigDisplay.vue'
import type { BudgetConfig } from '@/api/types'

const mockConfig: BudgetConfig = {
  total_monthly: 1000,
  per_agent_daily_limit: 50,
  per_task_limit: 10,
  reset_day: 1,
  alerts: {
    warn_at: 80,
    critical_at: 95,
    hard_stop_at: 100,
  },
  auto_downgrade: {
    enabled: false,
    threshold: 90,
    downgrade_map: [],
    boundary: 'task_assignment',
  },
}

describe('BudgetConfigDisplay', () => {
  it('renders nothing when config is null', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: null },
    })
    expect(wrapper.text()).toBe('')
  })

  it('shows monthly budget when config is provided', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    expect(wrapper.text()).toContain('Monthly Budget')
    // formatCurrency(1000) → "$1,000.00"
    expect(wrapper.text()).toContain('$1,000.00')
  })

  it('shows per agent daily limit', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    expect(wrapper.text()).toContain('Per Agent Daily')
    expect(wrapper.text()).toContain('$50.00')
  })

  it('shows per task limit', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    expect(wrapper.text()).toContain('Per Task Limit')
    expect(wrapper.text()).toContain('$10.00')
  })

  it('shows alert threshold percentage', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    expect(wrapper.text()).toContain('Alert Threshold')
    expect(wrapper.text()).toContain('80%')
  })

  it('shows reset day', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    expect(wrapper.text()).toContain('Reset Day')
    expect(wrapper.text()).toContain('1')
  })

  it('renders all five config sections', () => {
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: mockConfig },
    })
    const labels = wrapper.findAll('.text-xs.text-slate-500')
    expect(labels).toHaveLength(5)
  })

  it('formats different monetary values correctly', () => {
    const custom: BudgetConfig = {
      ...mockConfig,
      total_monthly: 2500.5,
      per_agent_daily_limit: 125.75,
      per_task_limit: 0.99,
    }
    const wrapper = mount(BudgetConfigDisplay, {
      props: { config: custom },
    })
    expect(wrapper.text()).toContain('$2,500.50')
    expect(wrapper.text()).toContain('$125.75')
    expect(wrapper.text()).toContain('$0.99')
  })
})
