import type { Meta, StoryObj } from '@storybook/react'
import { MeetingActionItems } from './MeetingActionItems'

const meta = {
  title: 'Meetings/MeetingActionItems',
  component: MeetingActionItems,
  tags: ['autodocs'],
  args: {
    participantNames: {
      'agent-alice': 'Alice Smith',
      'agent-bob': 'Bob Jones',
      'agent-carol': 'Carol Diaz',
    },
  },
} satisfies Meta<typeof MeetingActionItems>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    actionItems: [
      { description: 'Finish test coverage for auth module', assignee_id: 'agent-bob', priority: 'high' },
      { description: 'Update deployment docs', assignee_id: 'agent-alice', priority: 'medium' },
      { description: 'Review security audit findings', assignee_id: null, priority: 'critical' },
    ],
  },
}

export const SingleItem: Story = {
  args: {
    actionItems: [
      { description: 'Deploy to staging', assignee_id: 'agent-carol', priority: 'low' },
    ],
  },
}

export const Empty: Story = {
  args: { actionItems: [] },
}
