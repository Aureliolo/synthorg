import type { Meta, StoryObj } from '@storybook/react'
import { ActivityLog } from './ActivityLog'
import type { AgentActivityEvent } from '@/api/types/agents'

const FIXED_BASE = new Date('2026-03-26T12:00:00.000Z')

function makeActivities(count: number): AgentActivityEvent[] {
  const eventTypes = ['task_completed', 'task_started', 'cost_incurred', 'tool_used', 'hired'] as const
  return Array.from({ length: count }, (_, i) => ({
    event_type: eventTypes[i % eventTypes.length]!,
    timestamp: new Date(FIXED_BASE.getTime() - i * 120_000).toISOString(),
    description: `Activity event ${i + 1} description`,
    related_ids: { agent_id: 'agent-001' },
    actor_name: 'Anica Hocevar',
    subject_title: i % 2 === 0 ? 'Wire the login page' : null,
  }))
}

const meta = {
  title: 'Agents/ActivityLog',
  component: ActivityLog,
  decorators: [(Story) => <div className="p-6 max-w-lg"><Story /></div>],
} satisfies Meta<typeof ActivityLog>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    events: makeActivities(5),
    total: 5,
    onLoadMore: () => {},
  },
}

export const WithMoreToLoad: Story = {
  args: {
    events: makeActivities(5),
    total: 20,
    onLoadMore: () => {},
  },
}

export const Empty: Story = {
  args: {
    events: [],
    total: 0,
    onLoadMore: () => {},
  },
}

export const Full: Story = {
  args: {
    events: makeActivities(15),
    total: 15,
    onLoadMore: () => {},
  },
}
