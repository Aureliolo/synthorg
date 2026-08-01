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
      compose_set: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

describe('SettingRow: compose_set', () => {
  it('disables the input when the deployment fixed the value', () => {
    const entry = makeEntry({
      compose_set: true,
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    const input = screen.getByDisplayValue<HTMLInputElement>('127.0.0.1')
    expect(input.disabled).toBe(true)
  })

  it('renders the compose-set notice when applicable', () => {
    const entry = makeEntry({
      compose_set: true,
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    expect(screen.getByText(/Set by the deployment\./i)).toBeInTheDocument()
  })

  it('hides the compose-set notice when the env-locked notice is already shown', () => {
    // Env-locked source supersedes the compose-set notice so operators
    // do not see two overlapping read-only explanations.
    const entry = makeEntry({
      compose_set: true,
      source: 'env',
    })

    render(
      <SettingRow
        entry={entry}
        dirtyValue={undefined}
        onChange={() => {}}
        saving={false}
      />,
    )

    expect(screen.queryByText(/Set by the deployment\./i)).not.toBeInTheDocument()
    expect(
      screen.getByText(/Value set by environment variable/i),
    ).toBeInTheDocument()
  })

  it('describes the control itself, not just the surrounding group', () => {
    const entry = makeEntry({ compose_set: true })

    render(
      <SettingRow entry={entry} dirtyValue={undefined} onChange={() => {}} saving={false} />,
    )

    // Focus can land straight on the input, which never announces a
    // group-level description.
    const input = screen.getByDisplayValue<HTMLInputElement>('127.0.0.1')
    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    const notice = screen.getByText(/Set by the deployment\./i)
    expect(describedBy?.split(/\s+/)).toContain(notice.id)
  })
})
