import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { Checkbox } from './checkbox'

const meta = {
  title: 'UI/Checkbox',
  component: Checkbox,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof Checkbox>

export default meta
type Story = StoryObj<typeof meta>

export const Unchecked: Story = {
  args: { checked: false, 'aria-label': 'Select item' },
}

export const Checked: Story = {
  args: { checked: true, 'aria-label': 'Select item' },
}

export const Disabled: Story = {
  args: { checked: true, disabled: true, 'aria-label': 'Select item' },
}

function InteractiveCheckbox() {
  const [checked, setChecked] = useState(false)
  return (
    <Checkbox
      checked={checked}
      onCheckedChange={setChecked}
      aria-label="Select item"
    />
  )
}

export const Interactive: Story = {
  args: { 'aria-label': 'Select item' },
  render: () => <InteractiveCheckbox />,
}
