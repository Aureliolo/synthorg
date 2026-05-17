import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { MessageList } from './MessageList'
import type { Message, MessageMetadata } from '@/api/types/messages'

const baseMeta: MessageMetadata = { task_id: null, project_id: null, tokens_used: null, cost: null, extra: [] }
const threadMeta: MessageMetadata = { task_id: 'task-42', project_id: null, tokens_used: 500, cost: 0.01, extra: [] }

type MsgSpec = Omit<Message, 'parts' | 'attachments'> & {
  parts?: Message['parts']
}

const mk = (spec: MsgSpec): Message => ({
  ...spec,
  parts: spec.parts ?? [{ type: 'text', text: spec.text }],
  attachments: [],
})

const sampleMessages: Message[] = [
  mk({ id: '1', timestamp: '2026-03-28T09:00:00Z', sender: 'alice', to: '#eng', type: 'announcement', priority: 'normal', channel: '#eng', text: 'Sprint planning in 30 minutes.', metadata: baseMeta }),
  mk({ id: '2', timestamp: '2026-03-28T10:00:00Z', sender: 'bob', to: '#eng', type: 'delegation', priority: 'normal', channel: '#eng', text: 'Please implement the auth endpoint.', metadata: threadMeta }),
  mk({ id: '3', timestamp: '2026-03-28T10:30:00Z', sender: 'carol', to: '#eng', type: 'task_update', priority: 'normal', channel: '#eng', text: 'Working on it.', metadata: threadMeta }),
  mk({ id: '4', timestamp: '2026-03-28T14:00:00Z', sender: 'carol', to: '#eng', type: 'review_request', priority: 'high', channel: '#eng', text: 'PR ready for review.', parts: [{ type: 'text', text: 'PR ready for review.' }, { type: 'data', data: { ref: 'pr-42' } }], metadata: threadMeta }),
  mk({ id: '5', timestamp: '2026-03-27T16:00:00Z', sender: 'dave', to: '#eng', type: 'status_report', priority: 'normal', channel: '#eng', text: 'EOD: Completed database migration.', metadata: baseMeta }),
]

const meta: Meta<typeof MessageList> = {
  title: 'Pages/Messages/MessageList',
  component: MessageList,
  parameters: { a11y: { test: 'error' } },
  args: { onSelectMessage: fn(), onLoadMore: fn() },
  decorators: [(Story) => <div className="h-96 max-w-lg"><Story /></div>],
}
export default meta

type Story = StoryObj<typeof MessageList>

function InteractiveMessageList() {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  return (
    <MessageList
      messages={sampleMessages}
      expandedThreads={expanded}
      toggleThread={(id) => setExpanded((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })}
      onSelectMessage={fn()}
      hasMore={true}
      loadingMore={false}
      onLoadMore={fn()}
    />
  )
}

export const WithMessages: Story = {
  render: () => <InteractiveMessageList />,
}

export const Empty: Story = {
  args: { messages: [], expandedThreads: new Set(), toggleThread: fn(), hasMore: false, loadingMore: false },
}

export const LoadingMore: Story = {
  args: { messages: sampleMessages.slice(0, 2), expandedThreads: new Set(), toggleThread: fn(), hasMore: true, loadingMore: true },
}
