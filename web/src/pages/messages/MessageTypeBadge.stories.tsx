import type { Meta, StoryObj } from '@storybook/react'
import { MessageTypeBadge } from './MessageTypeBadge'
import type { MessageType } from '@/api/types/messages'

const meta: Meta<typeof MessageTypeBadge> = {
  title: 'Pages/Messages/MessageTypeBadge',
  component: MessageTypeBadge,
  parameters: { a11y: { test: 'error' } },
}
export default meta

type Story = StoryObj<typeof MessageTypeBadge>

const ALL_TYPES: MessageType[] = [
  'task_update',
  'question',
  'announcement',
  'review_request',
  'approval',
  'delegation',
  'status_report',
  'escalation',
  'hr_notification',
]

export const AllTypes: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {ALL_TYPES.map((type) => (
        <MessageTypeBadge key={type} type={type} />
      ))}
    </div>
  ),
}

export const TaskUpdate: Story = { args: { type: 'task_update' } }
export const Delegation: Story = { args: { type: 'delegation' } }
export const Escalation: Story = { args: { type: 'escalation' } }
