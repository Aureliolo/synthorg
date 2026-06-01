import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { TagInput } from './tag-input'

const meta: Meta<typeof TagInput> = {
  title: 'UI/TagInput',
  component: TagInput,
  parameters: { layout: 'centered' },
}
export default meta

type Story = StoryObj<typeof TagInput>

export const Default: Story = {
  args: { value: ['http://localhost:5173', 'https://example.com'], placeholder: 'Add origin...' },
  render: function Render(args) {
    const [value, setValue] = useState(args.value)
    return <TagInput {...args} value={value} onChange={setValue} />
  },
}

export const Empty: Story = {
  args: { value: [], placeholder: 'Add items...' },
  render: function Render(args) {
    const [value, setValue] = useState(args.value)
    return <TagInput {...args} value={value} onChange={setValue} />
  },
}

export const WithLabel: Story = {
  args: { value: ['task-1'], label: 'Task IDs', placeholder: 'Add a task ID...' },
  render: function Render(args) {
    const [value, setValue] = useState(args.value)
    return <TagInput {...args} value={value} onChange={setValue} />
  },
}

export const Disabled: Story = {
  args: { value: ['locked-value'], disabled: true },
}

export const ManyTags: Story = {
  args: {
    value: ['tag1', 'tag2', 'tag3', 'tag4', 'tag5', 'tag6', 'tag7', 'tag8'],
  },
  render: function Render(args) {
    const [value, setValue] = useState(args.value)
    return <TagInput {...args} value={value} onChange={setValue} />
  },
}
