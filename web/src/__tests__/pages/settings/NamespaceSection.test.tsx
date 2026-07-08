import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SettingEntry } from '@/api/types/settings'
import { NamespaceSection } from '@/pages/settings/NamespaceSection'

function makeEntry(
  overrides: Partial<SettingEntry['definition']> & { value?: string } = {},
): SettingEntry {
  const { value = 'v', ...defOverrides } = overrides
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
      ...defOverrides,
    },
    value,
    source: 'default',
    updated_at: null,
  }
}

const rowProps = {
  dirtyValues: new Map<string, string>(),
  onValueChange: () => {},
  savingKeys: new Map<string, number>(),
  controllerDisabledMap: new Map<string, boolean>(),
}

describe('NamespaceSection runtime partition', () => {
  it('keeps restart_required inline and moves read_only_post_init into Advanced', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={[
          makeEntry({ key: 'restart_key', value: 'restart-val', restart_required: true }),
          makeEntry({ key: 'readonly_key', value: 'readonly-val', read_only_post_init: true }),
        ]}
        {...rowProps}
      />,
    )

    // A restart_required setting is DB-writable, so it renders inline.
    expect(screen.getByDisplayValue('restart-val')).toBeInTheDocument()
    // The genuinely read-only setting sits in the collapsed Advanced disclosure,
    // so its row is not in the DOM until the disclosure is expanded.
    expect(screen.queryByDisplayValue('readonly-val')).not.toBeInTheDocument()
    expect(screen.getByText(/Advanced/)).toBeInTheDocument()
  })

  it('shows no Advanced disclosure when there are no read-only settings', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={[
          makeEntry({ key: 'restart_key', value: 'restart-val', restart_required: true }),
        ]}
        {...rowProps}
      />,
    )

    expect(screen.getByDisplayValue('restart-val')).toBeInTheDocument()
    expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument()
  })
})
