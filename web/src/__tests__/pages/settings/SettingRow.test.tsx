import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SettingEntry } from '@/api/types/settings'
import { SettingRow } from '@/pages/settings/SettingRow'

function makeEntry(
  overrides: Partial<SettingEntry['definition']> & {
    value?: string
    source?: SettingEntry['source']
  } = {},
): SettingEntry {
  const { value = '127.0.0.1', source = 'default', ...defOverrides } = overrides
  return {
    definition: {
      namespace: 'api',
      key: 'server_host',
      type: 'str',
      default: '127.0.0.1',
      description: 'Server bind address',
      group: 'Server',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      yaml_path: null,
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

describe('SettingRow: read_only_post_init', () => {
  it('disables the input when the definition is read-only post-init', () => {
    const entry = makeEntry({
      read_only_post_init: true,
      restart_required: true,
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    const input = screen.getByDisplayValue('127.0.0.1') as HTMLInputElement
    expect(input.disabled).toBe(true)
  })

  it('renders the post-init notice when applicable', () => {
    const entry = makeEntry({
      read_only_post_init: true,
      restart_required: true,
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    expect(
      screen.getByText(/Read-only after startup\./i),
    ).toBeInTheDocument()
  })

  it('hides the post-init notice when the env-locked notice is already shown', () => {
    // Env-locked source supersedes the post-init notice so operators
    // do not see two overlapping read-only explanations.
    const entry = makeEntry({
      read_only_post_init: true,
      restart_required: true,
      source: 'env',
    } as Partial<SettingEntry['definition']> & {
      source?: SettingEntry['source']
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    expect(
      screen.queryByText(/Read-only after startup\./i),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Value set by environment variable/i),
    ).toBeInTheDocument()
  })
})
