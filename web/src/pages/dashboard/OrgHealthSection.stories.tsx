import type { Meta, StoryObj } from '@storybook/react'
import { OrgHealthSection } from './OrgHealthSection'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { DepartmentName } from '@/api/types/enums'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

function makeDepts(configs: Array<{ name: DepartmentName; health: number }>): DepartmentHealth[] {
  return configs.map((c, i) => ({
    department_name: c.name,
    agent_count: 2 + i,
    active_agent_count: 1 + i,
    currency: DEFAULT_CURRENCY,
    avg_performance_score: 7.0 + i * 0.5,
    department_cost_7d: 10 + i * 3,
    cost_trend: [],
    collaboration_score: 6.0,
    total_runs: 10,
    task_success_rate: c.health / 100,
    utilization_percent: c.health,
    utilization_degraded: false,
    health_score: c.health,
  }))
}

function makeNoDataDepts(names: DepartmentName[]): DepartmentHealth[] {
  return names.map((name, i) => ({
    department_name: name,
    agent_count: 2 + i,
    active_agent_count: 1 + i,
    currency: DEFAULT_CURRENCY,
    avg_performance_score: null,
    department_cost_7d: 0,
    cost_trend: [],
    collaboration_score: null,
    total_runs: 0,
    task_success_rate: null,
    utilization_percent: 50 + i * 10,
    utilization_degraded: false,
    health_score: null,
  }))
}

const meta = {
  title: 'Dashboard/OrgHealthSection',
  component: OrgHealthSection,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof OrgHealthSection>

export default meta
type Story = StoryObj<typeof meta>

export const Healthy: Story = {
  args: {
    departments: makeDepts([
      { name: 'engineering', health: 92 },
      { name: 'design', health: 85 },
      { name: 'product', health: 78 },
    ]),
    departmentCount: 3,
    overallHealth: 85,
  },
}

export const Mixed: Story = {
  args: {
    departments: makeDepts([
      { name: 'engineering', health: 90 },
      { name: 'design', health: 45 },
      { name: 'operations', health: 20 },
      { name: 'security', health: 70 },
    ]),
    departmentCount: 4,
    overallHealth: 56,
  },
}

export const Empty: Story = {
  args: { departments: [], departmentCount: 0, overallHealth: null },
}

// The org has departments; their health could not be read. Distinct from
// Empty, which says the operator has not set the organisation up.
export const HealthUnavailable: Story = {
  args: { departments: [], departmentCount: 6, overallHealth: null },
}

// Departments exist but none has enough terminal runs to score yet: each bar
// reads "N/A" and the overall gauge is replaced by an explicit no-data note
// rather than a misleading full-health number.
export const NoData: Story = {
  args: {
    departments: makeNoDataDepts(['engineering', 'design']),
    departmentCount: 2,
    overallHealth: null,
  },
}
