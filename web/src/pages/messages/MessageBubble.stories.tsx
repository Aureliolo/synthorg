import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { MessageBubble } from './MessageBubble'
import type { Message } from '@/api/types/messages'

const textParts = (text: string): Message['parts'] => [{ type: 'text', text }]

const baseMessage: Message = {
  id: 'msg-1',
  timestamp: new Date(Date.now() - 300_000).toISOString(),
  sender: 'sarah_chen',
  to: '#engineering',
  type: 'task_update',
  priority: 'normal',
  channel: '#engineering',
  text: 'Completed API endpoint for user authentication. PR ready for review.',
  parts: textParts(
    'Completed API endpoint for user authentication. PR ready for review.',
  ),
  attachments: [],
  metadata: { task_id: null, project_id: null, tokens_used: 1200, cost: 0.018, extra: [] },
}

const meta: Meta<typeof MessageBubble> = {
  title: 'Pages/Messages/MessageBubble',
  component: MessageBubble,
  parameters: { a11y: { test: 'error' } },
  args: { onClick: fn() },
  decorators: [(Story) => <div className="max-w-lg"><Story /></div>],
}
export default meta

type Story = StoryObj<typeof MessageBubble>

export const Default: Story = {
  args: { message: baseMessage },
}

export const HighPriority: Story = {
  args: { message: { ...baseMessage, priority: 'high' } },
}

export const UrgentPriority: Story = {
  args: {
    message: {
      ...baseMessage,
      priority: 'urgent',
      type: 'escalation',
      text: 'Production database is unresponsive. Immediate attention required.',
      parts: textParts(
        'Production database is unresponsive. Immediate attention required.',
      ),
    },
  },
}

export const WithAttachments: Story = {
  args: {
    message: {
      ...baseMessage,
      parts: [
        ...textParts(baseMessage.text),
        { type: 'data', data: { ref: 'pr-42' } },
        { type: 'file', uri: 'coverage-report.html', mime_type: 'text/html' },
      ],
    },
  },
}

export const DelegationType: Story = {
  args: {
    message: {
      ...baseMessage,
      type: 'delegation',
      text: 'Delegating database migration task to junior developer. Please implement the schema changes as outlined in the design doc.',
      parts: textParts(
        'Delegating database migration task to junior developer. Please implement the schema changes as outlined in the design doc.',
      ),
    },
  },
}

export const NewMessage: Story = {
  args: { message: baseMessage, isNew: true },
}
