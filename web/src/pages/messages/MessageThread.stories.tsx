import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { MessageThread } from './MessageThread'
import type { Message, MessageMetadata } from '@/api/types/messages'

const sampleMetadata: MessageMetadata = {
  task_id: 'task-42',
  project_id: 'proj-1',
  tokens_used: 500,
  cost: 0.01,
  extra: [],
}

const threadMessages: Message[] = [
  {
    id: 'msg-1', timestamp: '2026-03-28T10:00:00Z', sender: 'sarah_chen', to: '#engineering',
    type: 'delegation', priority: 'normal', channel: '#engineering',
    text: 'Please implement the user auth endpoint.',
    parts: [{ type: 'text', text: 'Please implement the user auth endpoint.' }],
    attachments: [], metadata: sampleMetadata,
  },
  {
    id: 'msg-2', timestamp: '2026-03-28T10:30:00Z', sender: 'bob_dev', to: '#engineering',
    type: 'task_update', priority: 'normal', channel: '#engineering',
    text: 'Started working on it. Draft PR incoming.',
    parts: [{ type: 'text', text: 'Started working on it. Draft PR incoming.' }],
    attachments: [], metadata: sampleMetadata,
  },
  {
    id: 'msg-3', timestamp: '2026-03-28T11:00:00Z', sender: 'bob_dev', to: '#engineering',
    type: 'review_request', priority: 'normal', channel: '#engineering',
    text: 'PR ready for review.',
    parts: [
      { type: 'text', text: 'PR ready for review.' },
      { type: 'data', data: { ref: 'pr-42' } },
    ],
    attachments: [], metadata: sampleMetadata,
  },
]

const storyMeta: Meta<typeof MessageThread> = {
  title: 'Pages/Messages/MessageThread',
  component: MessageThread,
  parameters: { a11y: { test: 'error' } },
  args: { onToggle: fn(), onSelectMessage: fn() },
  decorators: [(Story) => <div className="max-w-lg"><Story /></div>],
}
export default storyMeta

type Story = StoryObj<typeof MessageThread>

export const Collapsed: Story = {
  args: { messages: threadMessages, expanded: false },
}

export const Expanded: Story = {
  args: { messages: threadMessages, expanded: true },
}

export const SingleMessage: Story = {
  args: { messages: [threadMessages[0]!], expanded: false },
}
