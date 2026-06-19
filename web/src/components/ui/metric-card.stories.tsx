import type { Meta, StoryObj } from '@storybook/react'
import { MetricCard } from './metric-card'
import { SkeletonMetric } from './skeleton'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'

const SAMPLE_SPEND = formatCurrency(12.5, DEFAULT_CURRENCY)

const meta = {
  title: 'UI/MetricCard',
  component: MetricCard,
  tags: ['autodocs'],
} satisfies Meta<typeof MetricCard>

export default meta
type Story = StoryObj<typeof meta>

const SAMPLE_DATA = [12, 15, 13, 18, 22, 19, 25, 28, 24, 30]

export const Default: Story = {
  args: { label: 'Tasks Today', value: 24 },
}

export const WithSparkline: Story = {
  args: {
    label: 'Tasks Today',
    value: 24,
    sparklineData: SAMPLE_DATA,
  },
}

export const WithChange: Story = {
  args: {
    label: 'Tasks Today',
    value: 24,
    change: { value: 12, direction: 'up' },
  },
}

export const WithNegativeChange: Story = {
  args: {
    label: 'Active Agents',
    value: 3,
    change: { value: 25, direction: 'down' },
  },
}

export const WithProgress: Story = {
  args: {
    label: 'Tasks Today',
    value: 24,
    progress: { current: 24, total: 30 },
    subText: 'of 30 completed',
  },
}

export const FullExample: Story = {
  args: {
    label: 'Tasks Today',
    value: 24,
    sparklineData: SAMPLE_DATA,
    change: { value: 12, direction: 'up' },
    progress: { current: 24, total: 30 },
    subText: 'of 30 completed',
  },
}

export const AnimatedValue: Story = {
  args: {
    label: 'Tasks Today',
    value: 24,
    animateValue: true,
    sparklineData: SAMPLE_DATA,
  },
}

export const Loading: Story = {
  args: { label: 'Tasks Today', value: 0 },
  render: () => <SkeletonMetric className="w-64" />,
}

export const StringValue: Story = {
  args: {
    label: 'Daily Spend',
    value: SAMPLE_SPEND,
    change: { value: 8, direction: 'down' },
    sparklineData: [30, 28, 25, 22, 18, 20, 15, 12],
  },
}

export const MetricGrid: Story = {
  args: { label: 'Tasks Today', value: 24 },
  render: () => (
    <div className="grid grid-cols-2 gap-grid-gap max-w-lg">
      <MetricCard
        label="Tasks Today"
        value={24}
        sparklineData={SAMPLE_DATA}
        change={{ value: 12, direction: 'up' }}
      />
      <MetricCard
        label="Active Agents"
        value={5}
        sparklineData={[3, 4, 5, 5, 4, 5, 5]}
      />
      <MetricCard
        label="Daily Spend"
        value={SAMPLE_SPEND}
        change={{ value: 8, direction: 'down' }}
      />
      <MetricCard
        label="Completion"
        value="80%"
        progress={{ current: 24, total: 30 }}
        subText="of 30 completed"
      />
    </div>
  ),
}
