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

// Send blocked (a precondition is unmet) while the field stays editable so the
// operator can keep composing.
export const SendDisabled: Story = {
  render: (args) => (
    <Controlled {...args} initialValue="Cut the cloud budget by 20%" disabled />
  ),
}

// Terminal state (e.g. a closed conversation): the field itself is frozen.
export const InputFrozen: Story = {
  args: { value: 'Cut the cloud budget by 20%', inputDisabled: true },
}
