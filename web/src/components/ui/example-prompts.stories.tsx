import type { Meta, StoryObj } from '@storybook/react'

import { ExamplePrompts } from './example-prompts'

const meta = {
  title: 'UI/ExamplePrompts',
  component: ExamplePrompts,
  tags: ['autodocs'],
  parameters: { layout: 'padded', a11y: { test: 'error' } },
  args: {
    prompts: [
      'What is the org working on right now?',
      'Why did spend increase this week?',
      'Which agents are underperforming?',
    ],
    onSelect: () => {},
    disabled: false,
  },
} satisfies Meta<typeof ExamplePrompts>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Disabled: Story = {
  args: { disabled: true },
}

export const SinglePrompt: Story = {
  args: { prompts: ['Describe your product idea in one sentence.'] },
}

export const Empty: Story = {
  args: { prompts: [] },
}
