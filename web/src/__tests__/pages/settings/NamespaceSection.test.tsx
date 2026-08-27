import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { makeSettingEntry as makeEntry } from '@/__tests__/helpers/factories'
import { NamespaceSection } from '@/pages/settings/NamespaceSection'

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
