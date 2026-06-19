import type { Meta, StoryObj } from '@storybook/react'
import {
  Skeleton,
  SkeletonCard,
  SkeletonChart,
  SkeletonMetric,
  SkeletonTable,
  SkeletonText,
} from './skeleton'

const meta = {
  title: 'Feedback/Skeleton',
  component: Skeleton,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
  },
} satisfies Meta<typeof Skeleton>

export default meta
type Story = StoryObj<typeof meta>

export const Base: Story = {
  render: () => <Skeleton className="h-8 w-48" />,
}

export const TextLines: Story = {
  render: () => <SkeletonText lines={3} />,
}

export const MetricCard: Story = {
  render: () => <SkeletonMetric className="w-64" />,
}

export const Card: Story = {
  render: () => <SkeletonCard header lines={3} className="w-80" />,
}

export const Table: Story = {
  render: () => <SkeletonTable rows={5} columns={4} />,
}

export const Chart: Story = {
  render: () => <SkeletonChart className="w-96" />,
}

export const NoShimmer: Story = {
  render: () => (
    <div className="space-y-4">
      <Skeleton shimmer={false} className="h-8 w-48" />
      <SkeletonText shimmer={false} />
      <SkeletonMetric shimmer={false} className="w-64" />
    </div>
  ),
}

export const GridLayout: Story = {
  render: () => (
    <div className="grid grid-cols-4 gap-4">
      {Array.from({ length: 4 }, (_, i) => (
        <SkeletonMetric key={i} />
      ))}
    </div>
  ),
}
