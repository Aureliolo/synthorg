import type { Meta, StoryObj } from '@storybook/react'

import { ChatBubble } from './chat-bubble'

const meta = {
  title: 'UI/ChatBubble',
  component: ChatBubble,
  tags: ['autodocs'],
  parameters: { layout: 'padded', a11y: { test: 'error' } },
  args: {
    variant: 'assistant',
    content: 'The runway is **7 months** at the current burn.',
    timestamp: new Date().toISOString(),
  },
} satisfies Meta<typeof ChatBubble>

export default meta
type Story = StoryObj<typeof meta>

export const Human: Story = {
  args: {
    variant: 'human',
    content: 'How much runway do we have?',
  },
}

export const Assistant: Story = {}

export const Streaming: Story = {
  args: {
    content: 'The runway is **7 months**',
    isStreaming: true,
  },
}

export const Agent: Story = {
  args: {
    variant: 'agent',
    agentName: 'Casey',
    agentRole: 'CFO',
    agentTopic: 'budget',
    content: 'About **7 months**. Two levers can extend it to nine.',
  },
}

export const Event: Story = {
  args: {
    variant: 'event',
    content: undefined,
    children: 'Brought in the CFO to answer this.',
  },
}

export const ErrorNotice: Story = {
  args: {
    variant: 'notice',
    content: undefined,
    isError: true,
    children: 'That turn failed. Try again.',
  },
}
