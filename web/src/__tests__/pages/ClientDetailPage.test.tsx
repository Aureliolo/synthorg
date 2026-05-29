import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { renderRoutes } from '@/__tests__/test-utils'
import type { ClientProfile } from '@/api/endpoints/clients'

let clients: readonly ClientProfile[]

vi.mock('@/hooks/useClientsData', () => ({
  useClientsData: () => ({ clients, loading: false, error: null, wsConnected: true, reload: vi.fn() }),
}))

const { default: ClientDetailPage } = await import('@/pages/ClientDetailPage')

function renderPage(clientId = 'c-1') {
  return renderRoutes([{ path: '/clients/:clientId', element: <ClientDetailPage /> }], {
    initialEntries: [`/clients/${clientId}`],
  })
}

describe('ClientDetailPage', () => {
  beforeEach(() => {
    clients = []
    vi.clearAllMocks()
  })

  it('renders the client profile from the default handler', async () => {
    renderPage()
    // The name appears in both the breadcrumb and the page heading.
    expect(await screen.findByRole('heading', { name: 'Default Client' })).toBeInTheDocument()
    expect(screen.getByText('Profile')).toBeInTheDocument()
  })

  it('renders the not-found error when the client cannot load', async () => {
    server.use(
      http.get('/api/v1/clients/:id', () =>
        HttpResponse.json(apiError('gone'), { status: 404 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Client not found')).toBeInTheDocument()
  })
})
