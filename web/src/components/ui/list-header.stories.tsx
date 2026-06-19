import type { Meta, StoryObj } from '@storybook/react'
import { Plus } from 'lucide-react'
import { Button } from './button'
import { ListHeader } from './list-header'

const meta = {
  title: 'Layout/ListHeader',
  component: ListHeader,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof ListHeader>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    title: 'Agents',
  },
}

export const WithCount: Story = {
  args: {
    title: 'Tasks',
    count: 247,
  },
}

export const WithCountAndAction: Story = {
  args: {
    title: 'Projects',
    count: 12,
    primaryAction: (
      <Button size="sm">
        <Plus aria-hidden="true" />
        New project
      </Button>
    ),
  },
}

export const WithDescription: Story = {
  args: {
    title: 'Providers',
    count: 5,
    description: 'Configure LLM providers available to your agents.',
    primaryAction: (
      <Button size="sm">
        <Plus aria-hidden="true" />
        Add provider
      </Button>
    ),
  },
}

export const WithSecondaryActions: Story = {
  args: {
    title: 'Approvals',
    count: 43,
    primaryAction: <Button size="sm" variant="outline">Batch actions</Button>,
    secondaryActions: (
      <>
        <div className="rounded-md border border-border bg-surface px-3 py-1.5 text-xs">Search: pending review</div>
        <div className="rounded-md border border-border bg-surface px-3 py-1.5 text-xs">Risk: all</div>
        <div className="rounded-md border border-border bg-surface px-3 py-1.5 text-xs">Sort: urgency</div>
      </>
    ),
  },
}

export const Refreshing: Story = {
  args: {
    title: 'Overview',
    count: 12,
    refreshing: true,
  },
}

export const LongTitle: Story = {
  args: {
    title: 'Department policy overrides with long name that should truncate',
    count: 3,
    primaryAction: <Button size="sm">Action</Button>,
  },
}
