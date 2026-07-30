import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { buildConnection } from '@/mocks/handlers/connections'
import { ConnectionFormModal } from '@/pages/connections/ConnectionFormModal'
import type { Connection } from '@/api/types/integrations'

/**
 * The form is metadata-driven, so its fields are only as correct as the registry
 * read it renders from. These tests drive the real registry fetch through MSW
 * rather than handing the form a fixture, which is what makes them able to catch
 * a control rendered for a type that cannot use it.
 */

function renderModal(
  overrides: { connection?: Connection; mode?: 'create' | 'edit' } = {},
) {
  const onClose = vi.fn()
  render(
    <ConnectionFormModal
      open={true}
      mode={overrides.mode ?? 'edit'}
      connection={overrides.connection ?? null}
      onClose={onClose}
    />,
  )
  return { onClose }
}

const RETENTION_LABEL = /Webhook receipt retention/

describe('ConnectionFormModal type picker', () => {
  it('lists the types the registry returns, with their own labels', async () => {
    renderModal({ mode: 'create' })
    expect(await screen.findByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('Database')).toBeInTheDocument()
  })

  it('describes each type from the registry rather than from a local table', async () => {
    renderModal({ mode: 'create' })
    expect(
      await screen.findByText('Access GitHub repositories, issues, and pull requests.'),
    ).toBeInTheDocument()
  })
})

describe('ConnectionFormModal webhook retention control', () => {
  it('offers retention for a type that can accumulate receipts', async () => {
    renderModal({
      connection: buildConnection({ name: 'primary', connection_type: 'github' }),
    })
    expect(await screen.findByLabelText(RETENTION_LABEL)).toBeInTheDocument()
  })

  it('withholds retention for a type that cannot receive a webhook', async () => {
    renderModal({
      connection: buildConnection({ name: 'primary', connection_type: 'database' }),
    })
    // The header's type badge only reads "Database" once the registry has
    // loaded, so the absence assertion below is made against a loaded registry
    // rather than an empty one.
    expect(await screen.findByText('Database')).toBeInTheDocument()
    expect(screen.queryByLabelText(RETENTION_LABEL)).not.toBeInTheDocument()
  })

  it('hydrates an existing per-connection retention override', async () => {
    renderModal({
      connection: buildConnection({
        name: 'primary',
        connection_type: 'github',
        webhook_receipt_retention_days: 30,
      }),
    })
    expect(await screen.findByLabelText(RETENTION_LABEL)).toHaveValue(30)
  })

  it('leaves the field blank when the connection follows the system default', async () => {
    // Blank and 0 mean different things (inherit vs never sweep), so a null
    // override must not hydrate as a number.
    renderModal({
      connection: buildConnection({
        name: 'primary',
        connection_type: 'github',
        webhook_receipt_retention_days: null,
      }),
    })
    expect(await screen.findByLabelText(RETENTION_LABEL)).toHaveValue(null)
  })
})
