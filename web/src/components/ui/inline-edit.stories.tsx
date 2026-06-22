import type { Meta, StoryObj } from '@storybook/react'
import { InlineEdit } from './inline-edit'

const meta = {
  title: 'Input/InlineEdit',
  component: InlineEdit,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
  },
} satisfies Meta<typeof InlineEdit>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    value: 'Agent Alpha',
    onSave: async (v) => {
      await new Promise((resolve) => setTimeout(resolve, 500))
      console.log('Saved:', v)
    },
  },
}

export const WithValidation: Story = {
  args: {
    value: 'Required field',
    onSave: async () => {},
    validate: (v) => (v.trim().length === 0 ? 'This field is required' : null),
  },
}

export const SaveError: Story = {
  args: {
    value: 'Will fail',
    onSave: async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
      throw new Error('Network error: could not save')
    },
  },
}

export const SimulatedLoading: Story = {
  args: {
    value: 'Slow save',
    onSave: async () => {
      await new Promise((resolve) => setTimeout(resolve, 3000))
    },
  },
}

export const Disabled: Story = {
  args: {
    value: 'Cannot edit',
    onSave: async () => {},
    disabled: true,
  },
}

export const CustomDisplay: Story = {
  args: {
    value: 'agent-cfo-001',
    onSave: async () => {},
    renderDisplay: (v) => <code className="font-mono text-accent">{v}</code>,
  },
}

export const NumberInput: Story = {
  args: {
    value: '42',
    onSave: async () => {},
    type: 'number',
  },
}

// External label: a visible `<label htmlFor>` associates with the widget via
// the `id` prop so screen readers announce the field name.
export const WithExternalLabel: Story = {
  args: {
    id: 'inline-field',
    value: 'Agent Alpha',
    onSave: async () => {},
  },
  render: (args) => (
    <div className="flex flex-col gap-1">
      <label htmlFor="inline-field" className="text-sm font-medium text-foreground">
        Agent name
      </label>
      <InlineEdit {...args} />
    </div>
  ),
  parameters: { controls: { disable: true } },
}
