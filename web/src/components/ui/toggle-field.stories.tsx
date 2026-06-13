import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { ToggleField } from './toggle-field'

const meta = {
  title: 'UI/ToggleField',
  component: ToggleField,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof ToggleField>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { label: 'Enable feature', checked: false, onChange: () => {} },
}

export const Checked: Story = {
  args: { label: 'Enable feature', checked: true, onChange: () => {} },
}

export const WithDescription: Story = {
  args: {
    label: 'Set a budget limit',
    description: 'Budget enforcement prevents agents from exceeding this limit.',
    checked: false,
    onChange: () => {},
  },
}

export const Disabled: Story = {
  args: { label: 'Enable feature', checked: true, disabled: true, onChange: () => {} },
}

function InteractiveToggle({ label, description }: { label: string; description?: string | undefined }) {
  const [checked, setChecked] = useState(false)
  return <ToggleField label={label} description={description} checked={checked} onChange={setChecked} />
}

export const Interactive: Story = {
  args: { label: 'Set a budget limit', description: 'Budget enforcement prevents agents from exceeding this limit.', checked: false, onChange: () => {} },
  render: (args) => <InteractiveToggle label={args.label} description={args.description} />,
}
