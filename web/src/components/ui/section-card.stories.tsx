import type { Meta, StoryObj } from '@storybook/react'
import { Inbox, Settings } from 'lucide-react'
import { SectionCard } from './section-card'
import { Button } from './button'
import { EmptyState } from './empty-state'
import { ErrorBanner } from './error-banner'
import { SkeletonText } from './skeleton'

const meta = {
  title: 'UI/SectionCard',
  component: SectionCard,
  tags: ['autodocs'],
} satisfies Meta<typeof SectionCard>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    title: 'Overview',
    children: 'Section content goes here.',
  },
}

export const WithIcon: Story = {
  args: {
    title: 'Settings',
    icon: Settings,
    children: 'Settings content goes here.',
  },
}

export const WithAction: Story = {
  args: {
    title: 'Agents',
    action: <Button size="xs" variant="ghost">View All</Button>,
    children: 'Agent list would appear here.',
  },
}

export const WithIconAndAction: Story = {
  args: {
    title: 'Settings',
    icon: Settings,
    action: <Button size="xs" variant="ghost">Edit</Button>,
    children: 'Settings content goes here.',
  },
}

export const NestedContent: Story = {
  args: { title: 'Department Health', children: null },
  render: () => (
    <SectionCard title="Department Health">
      <div className="flex flex-col gap-3 text-sm text-text-secondary">
        <p>Engineering: 92%</p>
        <p>Marketing: 78%</p>
        <p>Sales: 65%</p>
      </div>
    </SectionCard>
  ),
}

// The card body cycles through these three states on every data-driven page:
// skeleton while loading, an inline error banner on fetch failure, and an
// empty state when the list is empty.
export const Loading: Story = {
  args: { title: 'Department Health', children: null },
  render: () => (
    <SectionCard title="Department Health">
      <SkeletonText lines={3} />
    </SectionCard>
  ),
}

export const ErrorContent: Story = {
  args: { title: 'Department Health', children: null },
  render: () => (
    <SectionCard title="Department Health">
      <ErrorBanner
        variant="section"
        title="Could not load department health"
        description="The analytics service is unavailable. Retry in a moment."
      />
    </SectionCard>
  ),
}

export const Empty: Story = {
  args: { title: 'Department Health', children: null },
  render: () => (
    <SectionCard title="Department Health">
      <EmptyState
        icon={Inbox}
        title="No departments yet"
        description="Departments appear here once your organisation is configured."
      />
    </SectionCard>
  ),
}
