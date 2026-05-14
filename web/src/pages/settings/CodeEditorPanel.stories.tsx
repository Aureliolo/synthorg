import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import type { SettingEntry } from '@/api/types/settings'
import { CodeEditorPanel } from './CodeEditorPanel'

function makeSetting(
  overrides: Partial<SettingEntry['definition']> & {
    value?: string
    source?: SettingEntry['source']
  } = {},
): SettingEntry {
  const { value = '3001', source = 'default', ...defOverrides } = overrides
  return {
    definition: {
      namespace: 'api',
      key: 'server_port',
      type: 'int',
      default: '3001',
      description: 'Server bind port',
      group: 'Server',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: 1,
      max_value: 65535,
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

const mockEntries: SettingEntry[] = [
  makeSetting(),
  makeSetting({
    key: 'rate_limit_max_requests',
    description: 'Maximum requests per time window',
    group: 'Rate Limiting',
    min_value: 1,
    max_value: 10000,
    value: '100',
  }),
  makeSetting({
    namespace: 'budget',
    key: 'total_monthly',
    type: 'float',
    description: 'Monthly budget limit',
    group: 'Limits',
    min_value: 0,
    max_value: null,
    value: '100.0',
  }),
]

const meta = {
  title: 'Settings/CodeEditorPanel',
  component: CodeEditorPanel,
  tags: ['autodocs'],
  parameters: { layout: 'padded' },
} satisfies Meta<typeof CodeEditorPanel>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    entries: mockEntries,
    onSave: async () => new Set<string>(),
    saving: false,
  },
}

export const Saving: Story = {
  args: {
    entries: mockEntries,
    onSave: async () => new Set<string>(),
    saving: true,
  },
}

export const Interactive: Story = {
  args: {
    entries: mockEntries,
    onSave: async () => new Set<string>(),
    saving: false,
  },
  render: function InteractivePanel() {
    const [saving, setSaving] = useState(false)
    return (
      <CodeEditorPanel
        entries={mockEntries}
        onSave={async () => {
          setSaving(true)
          await new Promise((r) => setTimeout(r, 1000))
          setSaving(false)
          return new Set<string>()
        }}
        saving={saving}
      />
    )
  },
}
