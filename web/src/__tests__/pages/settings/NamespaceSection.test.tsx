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
      compose_set: false,
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
  const mixedEntries = [
    makeEntry({ key: 'live_key', value: 'live-val' }),
    makeEntry({ key: 'fixed_key', value: 'fixed-val', compose_set: true }),
  ]

  it('keeps live settings inline and moves compose-set ones into the disclosure', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={mixedEntries}
        {...rowProps}
      />,
    )

    // A live setting is DB-writable, so it renders inline.
    expect(screen.getByDisplayValue('live-val')).toBeInTheDocument()
    // The compose-set setting sits in the collapsed disclosure, so its row is
    // not in the DOM until the disclosure is expanded.
    expect(screen.queryByDisplayValue('fixed-val')).not.toBeInTheDocument()
    expect(screen.getByText(/Set by the deployment/)).toBeInTheDocument()
  })

  it('shows no compose-set disclosure when every setting is live', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={[makeEntry({ key: 'live_key', value: 'live-val' })]}
        {...rowProps}
      />,
    )

    expect(screen.getByDisplayValue('live-val')).toBeInTheDocument()
    expect(screen.queryByText(/Set by the deployment/)).not.toBeInTheDocument()
  })

  it('expands the compose-set disclosure while a search is active', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={mixedEntries}
        {...rowProps}
        highlightQuery="fixed"
      />,
    )

    // A match inside a collapsed disclosure would otherwise be invisible.
    expect(screen.getByDisplayValue('fixed-val')).toBeInTheDocument()
  })

  it('does not collide with the page-level Advanced skill-level toggle', () => {
    render(
      <NamespaceSection
        displayName="Api"
        icon={null}
        hideHeader
        entries={mixedEntries}
        {...rowProps}
      />,
    )

    expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument()
  })
})
