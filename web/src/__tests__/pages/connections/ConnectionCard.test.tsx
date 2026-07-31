import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { buildConnection } from '@/mocks/handlers/connections'
import { ConnectionCard } from '@/pages/connections/ConnectionCard'
import type { Connection, HealthReport } from '@/api/types/integrations'

/**
 * The card renders on its own, with no form modal mounted beside it.
 *
 * That is the point of these tests: the receipts cross-link is driven by the
 * connection-type registry, and the registry used to be fetched only by an
 * effect inside the form. Rendering the card alone is what proves the link no
 * longer depends on a sibling component having been mounted first.
 */

function report(overrides: Partial<HealthReport> = {}): HealthReport {
  return {
    connection_name: 'primary',
    status: 'healthy',
    latency_ms: 42,
    error_detail: null,
    checked_at: '2026-04-12T08:00:00Z',
    consecutive_failures: 0,
    webhook_ingest: 'ready',
    retry_after_seconds: null,
    ...overrides,
  }
}

function renderCard(connection: Connection, health: HealthReport | null = report()) {
  render(
    <MemoryRouter>
      <ConnectionCard
        connection={connection}
        report={health}
        checking={false}
        onRunHealthCheck={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />
    </MemoryRouter>,
  )
}

const RECEIPTS_LINK = 'View webhook receipts'
const INGEST_WARNING = /No webhook signing secret/

describe('ConnectionCard webhook affordances', () => {
  it('offers the receipts link for a type that can receive webhooks', async () => {
    renderCard(buildConnection({ name: 'primary', connection_type: 'github' }))
    expect(await screen.findByText(RECEIPTS_LINK)).toBeInTheDocument()
  })

  it('omits the receipts link for a type that cannot', async () => {
    renderCard(buildConnection({ name: 'primary', connection_type: 'database' }))
    // Awaiting the type badge's registry label, so the assertion runs after the
    // registry has loaded rather than passing on an empty one.
    expect(await screen.findByText('Database')).toBeInTheDocument()
    expect(screen.queryByText(RECEIPTS_LINK)).not.toBeInTheDocument()
  })

  it('warns when a reachable ingest path has no signing secret', () => {
    renderCard(
      buildConnection({ name: 'primary', connection_type: 'github' }),
      report({ webhook_ingest: 'unconfigured' }),
    )
    expect(screen.getByText(INGEST_WARNING)).toBeInTheDocument()
  })

  it('stays quiet when the signing secret is set', () => {
    renderCard(buildConnection({ name: 'primary', connection_type: 'github' }))
    expect(screen.queryByText(INGEST_WARNING)).not.toBeInTheDocument()
  })

  it('stays quiet before any health report has arrived', () => {
    // A missing report is "not checked yet", not "misconfigured": claiming an
    // unset secret here would warn on every connection at first paint.
    renderCard(buildConnection({ name: 'primary', connection_type: 'github' }), null)
    expect(screen.queryByText(INGEST_WARNING)).not.toBeInTheDocument()
  })
})
