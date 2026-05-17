import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { MessageDetailDrawer } from './MessageDetailDrawer'
import type { Message } from '@/api/types/messages'

const fullMessage: Message = {
  id: 'msg-1',
  timestamp: '2026-03-28T10:30:00Z',
  sender: 'sarah_chen',
  to: '#engineering',
  type: 'task_update',
  priority: 'normal',
  channel: '#engineering',
  text: 'Completed API endpoint for user authentication. All tests pass, coverage at 95%. PR ready for review.\n\nKey changes:\n- Added JWT validation middleware\n- Created user session store\n- Updated OpenAPI schema',
  parts: [
    {
      type: 'text',
      text: 'Completed API endpoint for user authentication. All tests pass, coverage at 95%. PR ready for review.\n\nKey changes:\n- Added JWT validation middleware\n- Created user session store\n- Updated OpenAPI schema',
    },
    { type: 'data', data: { ref: 'pr-42' } },
    { type: 'file', uri: 'coverage-report.html', mime_type: 'text/html' },
  ],
  attachments: [],
  metadata: {
    task_id: 'task-123',
    project_id: 'proj-456',
    tokens_used: 1200,
    cost: 0.018,
    extra: [['model', 'test-medium-001'], ['latency_ms', '2340']],
  },
}

const meta: Meta<typeof MessageDetailDrawer> = {
  title: 'Pages/Messages/MessageDetailDrawer',
  component: MessageDetailDrawer,
  parameters: { a11y: { test: 'error' } },
  args: { onClose: fn() },
}
export default meta

type Story = StoryObj<typeof MessageDetailDrawer>

export const FullMetadata: Story = {
  args: { message: fullMessage, open: true },
}

export const MinimalMetadata: Story = {
  args: {
    message: {
      ...fullMessage,
      parts: [{ type: 'text', text: fullMessage.text }],
      metadata: { task_id: null, project_id: null, tokens_used: null, cost: null, extra: [] },
    },
    open: true,
  },
}

export const WithAttachments: Story = {
  args: {
    message: {
      ...fullMessage,
      metadata: {
        task_id: null,
        project_id: null,
        tokens_used: null,
        cost: null,
        extra: [],
      },
      parts: [
        { type: 'text', text: fullMessage.text },
        { type: 'data', data: { ref: 'pr-42' } },
        { type: 'file', uri: 'coverage-report.html', mime_type: 'text/html' },
        { type: 'uri', uri: 'https://example.com/docs' },
      ],
    },
    open: true,
  },
}

export const Closed: Story = {
  args: { message: null, open: false },
}
