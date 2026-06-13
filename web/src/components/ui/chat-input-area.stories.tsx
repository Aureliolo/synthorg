import { useState } from 'react'
import type { ComponentProps } from 'react'
import type { Meta, StoryObj } from '@storybook/react'

import { ChatInputArea } from './chat-input-area'

const meta = {
  title: 'UI/ChatInputArea',
  component: ChatInputArea,
  tags: ['autodocs'],
  parameters: { layout: 'padded', a11y: { test: 'error' } },
  args: {
    value: '',
    label: 'Message',
    placeholder: 'Type a message...',
    onChange: () => {},
    onSend: () => {},
    disabled: false,
  },
} satisfies Meta<typeof ChatInputArea>

export default meta
type Story = StoryObj<typeof meta>

function Controlled({
  initialValue = '',
  ...args
}: Omit<ComponentProps<typeof ChatInputArea>, 'value' | 'onChange'> & {
  initialValue?: string
}) {
  const [value, setValue] = useState(initialValue)
  return <ChatInputArea {...args} value={value} onChange={setValue} />
}

export const Empty: Story = {
  render: (args) => <Controlled {...args} />,
}

export const WithValue: Story = {
  render: (args) => (
    <Controlled {...args} initialValue="Cut the cloud budget by 20%" />
  ),
}

export const Disabled: Story = {
  args: { value: 'Sending...', disabled: true },
}

// In-flight send: the box keeps the message visible but disables the field and
// send button (the send affordance is managed purely via `disabled`).
export const Sending: Story = {
  args: { value: 'Cut the cloud budget by 20%', disabled: true },
}
