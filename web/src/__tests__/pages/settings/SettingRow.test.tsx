import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { SettingEntry } from '@/api/types/settings'
import { makeSettingEntry } from '@/__tests__/helpers/factories'
import { SettingRow } from '@/pages/settings/SettingRow'

// The row renders the effective value, so it starts at the default rather
// than the placeholder every other suite is happy with.
function makeEntry(
  overrides: Partial<SettingEntry['definition']> & {
    value?: string
    source?: SettingEntry['source']
  } = {},
): SettingEntry {
  return makeSettingEntry({ value: '127.0.0.1', ...overrides })
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

  it('shows the compose-set notice rather than the env one for an env source', () => {
    // A deployment passes compose-set values as environment variables, so
    // this pair is the normal case rather than an overlap; the generic env
    // notice would replace the one saying how to change it.
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

    expect(screen.getByText(/Set by the deployment\./i)).toBeInTheDocument()
    expect(
      screen.queryByText(/Value set by environment variable/i),
    ).not.toBeInTheDocument()
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
