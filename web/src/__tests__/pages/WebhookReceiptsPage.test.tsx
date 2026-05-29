import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Connection } from '@/api/types/integrations'
import type { listWebhookActivity } from '@/api/endpoints/webhooks'

let connections: readonly Connection[]

vi.mock('@/hooks/useConnectionsData', () => ({
  useConnectionsData: () => ({ connections }),
}))

const { default: WebhookReceiptsPage } = await import('@/pages/WebhookReceiptsPage')

function makeConnection(name: string): Connection {
  return { name } as unknown as Connection
}

function renderPage() {
  return render(
    <MemoryRouter>
      <WebhookReceiptsPage />
    </MemoryRouter>,
  )
}

describe('WebhookReceiptsPage', () => {
  beforeEach(() => {
    connections = []
    vi.clearAllMocks()
  })

  it('renders the page heading', () => {
    renderPage()
    // Appears in both the breadcrumb trail and the list header.
    expect(screen.getAllByText('Webhook receipts').length).toBeGreaterThanOrEqual(1)
  })

  it('shows the no-connections empty state when there are no connections', () => {
    connections = []
    renderPage()
    expect(screen.getByText('No connections configured')).toBeInTheDocument()
  })

  it('renders the receipts table once a connection is selected and activity loads', async () => {
    connections = [makeConnection('slack-app')]
    renderPage()
    // The default activity handler returns rows, so the table renders.
    expect(await screen.findByText('Recent receipts')).toBeInTheDocument()
  })

  it('shows the no-deliveries empty state when activity is empty', async () => {
    connections = [makeConnection('slack-app')]
    server.use(
      http.get('/api/v1/webhooks/:connectionName/activity', () =>
        HttpResponse.json(successFor<typeof listWebhookActivity>([])),
      ),
    )
    renderPage()
    expect(await screen.findByText('No webhook deliveries yet')).toBeInTheDocument()
  })
})
