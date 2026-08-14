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

// A value held by the form that is not among the choices: a persisted setting
// whose option has since been removed, or a stale API value. The control shows
// it as itself rather than silently displaying the first option, and says it
// cannot be used.
export const UnmatchedValue: Story = {
  args: {
    label: 'Currency',
    options: currencies,
    value: 'CHF',
    onChange: () => {},
  },
}

export const UnsetWithoutPlaceholder: Story = {
  args: { label: 'Currency', options: currencies, value: '', onChange: () => {} },
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
        label: 'example-provider · example-expert',
        options: [
          { value: 'p::example-expert-002', label: 'example-expert-002 (200k · tools)' },
          { value: 'p::example-expert-001', label: 'example-expert-001 (200k)' },
        ],
      },
      {
        label: 'example-provider · example-basic',
        options: [{ value: 'p::example-basic-001', label: 'example-basic-001 (32k)' }],
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
