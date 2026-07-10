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
    health_score: c.health,
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
    overallHealth: 56,
  },
}

export const Empty: Story = {
  args: { departments: [], overallHealth: null },
}
