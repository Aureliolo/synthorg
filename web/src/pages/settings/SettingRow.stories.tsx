import type { Meta, StoryObj } from '@storybook/react'
import type { SettingEntry } from '@/api/types/settings'
import { SettingRow } from './SettingRow'

const meta: Meta<typeof SettingRow> = {
  title: 'Settings/SettingRow',
  component: SettingRow,
}
export default meta

type Story = StoryObj<typeof SettingRow>

function makeSetting(
  overrides: Partial<SettingEntry['definition']> & { value?: string; source?: SettingEntry['source'] } = {},
): SettingEntry {
  const { value = 'test', source = 'default', ...defOverrides } = overrides
  return {
    value,
    source,
    updated_at: null,
    definition: {
      namespace: 'api',
      key: 'server_host',
      type: 'str',
      default: '127.0.0.1',
      description: 'Server bind address',
      group: 'Server',
      level: 'basic',
      sensitive: false,
      compose_set: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      ...defOverrides,
    },
  }
}

export const Default: Story = {
  args: {
    entry: makeSetting({ value: '127.0.0.1' }),
    onChange: () => {},
    saving: false,
  },
}

export const Modified: Story = {
  args: {
    entry: makeSetting({ value: '0.0.0.0', source: 'db' }),
    onChange: () => {},
    saving: false,
  },
}

export const EnvLocked: Story = {
  args: {
    entry: makeSetting({ value: '0.0.0.0', source: 'env' }),
    onChange: () => {},
    saving: false,
  },
}

export const Disabled: Story = {
  args: {
    entry: makeSetting(),
    onChange: () => {},
    saving: false,
    controllerDisabled: true,
  },
}

export const ComposeSet: Story = {
  args: {
    entry: makeSetting({ compose_set: true }),
    onChange: () => {},
    saving: false,
  },
}
