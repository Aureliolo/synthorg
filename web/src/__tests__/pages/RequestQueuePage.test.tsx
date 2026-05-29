import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { emptyPage, paginatedFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Capabilities } from '@/api/types/capabilities'
import type { ClientRequest, listRequests } from '@/api/endpoints/clients'

interface CapReturn {
  capabilities: Capabilities
  loading: boolean
  error: string | null
}

let capReturn: CapReturn

vi.mock('@/hooks/useCapabilities', () => ({
  useCapabilities: () => capReturn,
}))

const { default: RequestQueuePage } = await import('@/pages/RequestQueuePage')

const ALL_ENABLED: Capabilities = {
  simulations: true,
  requests: true,
  ontology: true,
  tunnel: true,
  webhooks: true,
  a2a: true,
  telemetry: false,
  integrations: true,
}

function makeRequest(overrides: Partial<ClientRequest> = {}): ClientRequest {
  return {
    request_id: 'req-1',
    client_id: 'client-1',
    requirement: {
      title: 'Build a thing',
      description: 'A requirement',
      task_type: 'development',
      priority: 'medium',
      estimated_complexity: 'medium',
      acceptance_criteria: [],
    },
    status: 'submitted',
    created_at: '2026-04-19T00:00:00Z',
    metadata: {},
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <RequestQueuePage />
    </MemoryRouter>,
  )
}

describe('RequestQueuePage', () => {
  beforeEach(() => {
    capReturn = { capabilities: ALL_ENABLED, loading: false, error: null }
    vi.clearAllMocks()
  })

  it('renders the cap-error fallback when capability detection fails', () => {
    capReturn = { capabilities: ALL_ENABLED, loading: false, error: 'capability fetch failed' }
    renderPage()
    expect(screen.getByText('Could not determine available features')).toBeInTheDocument()
  })

  it('renders the not-configured fallback when requests are disabled', () => {
    capReturn = { capabilities: { ...ALL_ENABLED, requests: false }, loading: false, error: null }
    renderPage()
    expect(screen.getByText('Requests not configured')).toBeInTheDocument()
  })

  it('renders the loading fallback while capabilities resolve', () => {
    capReturn = { capabilities: ALL_ENABLED, loading: true, error: null }
    renderPage()
    expect(screen.queryByText('Requests not configured')).not.toBeInTheDocument()
    expect(screen.queryByText('No requests yet')).not.toBeInTheDocument()
  })

  it('renders the empty state once enabled and the fetch returns no requests', async () => {
    // The default /requests handler returns an empty page.
    renderPage()
    expect(await screen.findByText('No requests yet')).toBeInTheDocument()
  })

  it('renders the kanban board grouped by status when requests load', async () => {
    server.use(
      http.get('/api/v1/requests', () =>
        HttpResponse.json(
          paginatedFor<typeof listRequests>({
            ...emptyPage<ClientRequest>(),
            data: [makeRequest({ status: 'submitted' })],
          }),
        ),
      ),
    )
    renderPage()
    // The request card (with its requirement title) renders inside the
    // grouped board once data lands.
    expect(await screen.findByText('Build a thing')).toBeInTheDocument()
    expect(screen.queryByText('No requests yet')).not.toBeInTheDocument()
  })
})
