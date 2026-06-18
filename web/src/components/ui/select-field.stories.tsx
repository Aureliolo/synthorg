import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { SelectField } from './select-field'
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from '@/utils/currencies'

const currencies = CURRENCY_OPTIONS.slice(0, 4)

const meta = {
  title: 'UI/SelectField',
  component: SelectField,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof SelectField>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { label: 'Currency', options: currencies, value: DEFAULT_CURRENCY, onChange: () => {} },
}

export const WithPlaceholder: Story = {
  args: {
    label: 'Provider',
    options: [
      { value: 'openai', label: 'OpenAI-compatible' },
      { value: 'ollama', label: 'Ollama (local)' },
    ],
    value: '',
    onChange: () => {},
    placeholder: 'Select a provider...',
  },
}

export const WithError: Story = {
  args: {
    label: 'Currency',
    options: currencies,
    value: '',
    onChange: () => {},
    error: 'Please select a currency',
    required: true,
  },
}

export const WithHint: Story = {
  args: {
    label: 'Currency',
    options: currencies,
    value: DEFAULT_CURRENCY,
    onChange: () => {},
    hint: 'Display only; providers price in their own currency.',
  },
}

export const Disabled: Story = {
  args: { label: 'Currency', options: currencies, value: DEFAULT_CURRENCY, onChange: () => {}, disabled: true },
}

export const Grouped: Story = {
  args: {
    label: 'Model',
    value: '',
    onChange: () => {},
    placeholder: 'Select a model...',
    groups: [
      {
        label: 'example-provider · example-large',
        options: [
          { value: 'p::example-large-002', label: 'example-large-002 (200k · tools)' },
          { value: 'p::example-large-001', label: 'example-large-001 (200k)' },
        ],
      },
      {
        label: 'example-provider · example-small',
        options: [{ value: 'p::example-small-001', label: 'example-small-001 (32k)' }],
      },
    ],
  },
}

function InteractiveSelect() {
  const [value, setValue] = useState<string>(DEFAULT_CURRENCY)
  return <SelectField label="Currency" options={currencies} value={value} onChange={setValue} />
}

export const Interactive: Story = {
  args: { label: 'Currency', options: currencies, value: DEFAULT_CURRENCY, onChange: () => {} },
  render: () => <InteractiveSelect />,
}
