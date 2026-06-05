import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { ClientProfile } from '@/api/endpoints/clients'

interface ClientsData {
  clients: readonly ClientProfile[]
  loading: boolean
  error: string | null
  wsConnected: boolean
  reload: () => void
}

let hookReturn: ClientsData

vi.mock('@/hooks/useClientsData', () => ({
  useClientsData: () => hookReturn,
}))

const { default: ClientListPage } = await import('@/pages/ClientListPage')

function makeClient(overrides: Partial<ClientProfile> = {}): ClientProfile {
  return {
    client_id: 'c-1',
    name: 'Acme Corp',
    persona: 'pragmatic_startup_cto',
    expertise_domains: [],
    strictness_level: 5,
    ...overrides,
  }
}

const defaultReturn: ClientsData = {
  clients: [],
  loading: false,
  error: null,
  wsConnected: true,
  reload: vi.fn(),
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ClientListPage />
    </MemoryRouter>,
  )
}

describe('ClientListPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultReturn }
    vi.clearAllMocks()
  })

  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Clients')).toBeInTheDocument()
  })

  it('renders the loading skeleton (not the empty state) when loading with no data', () => {
    hookReturn = { ...defaultReturn, loading: true }
    renderPage()
    // The loading guard short-circuits before the empty state, so the
    // "No clients yet" copy must not render while loading.
    expect(screen.getByText('Clients')).toBeInTheDocument()
    expect(screen.queryByText('No clients yet')).not.toBeInTheDocument()
  })

  it('renders the empty state when there are no clients', () => {
    renderPage()
    expect(screen.getByText('No clients yet')).toBeInTheDocument()
  })

  it('renders client cards when data is available', () => {
    hookReturn = {
      ...defaultReturn,
      clients: [makeClient({ client_id: 'c-1', name: 'Acme Corp' }), makeClient({ client_id: 'c-2', name: 'Globex' })],
    }
    renderPage()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Globex')).toBeInTheDocument()
  })

  it('renders the error banner when error is set', () => {
    hookReturn = { ...defaultReturn, error: 'Network down' }
    renderPage()
    expect(screen.getByText('Could not load clients')).toBeInTheDocument()
    expect(screen.getByText('Network down')).toBeInTheDocument()
  })

  it('renders the offline banner when the websocket is disconnected', () => {
    hookReturn = { ...defaultReturn, wsConnected: false, clients: [makeClient()] }
    renderPage()
    expect(screen.getByText('Real-time updates disconnected')).toBeInTheDocument()
  })

  it('shows the filtered-empty message when a search matches nothing', () => {
    hookReturn = { ...defaultReturn, clients: [makeClient({ name: 'Acme Corp' })] }
    renderPage()
    fireEvent.change(screen.getByLabelText('Search clients'), {
      target: { value: 'no-such-client' },
    })
    expect(screen.getByText('No matching clients')).toBeInTheDocument()
    // The truly-empty copy must not show while a filter is active.
    expect(screen.queryByText('No clients yet')).not.toBeInTheDocument()
  })

  it('bulk-deactivates a selected client through the confirm dialog', async () => {
    let deletedId: string | undefined
    server.use(
      http.delete('/api/v1/clients/:id', ({ params }) => {
        deletedId = String(params.id)
        return HttpResponse.json(voidSuccess())
      }),
    )
    hookReturn = {
      ...defaultReturn,
      clients: [makeClient({ client_id: 'c-1', name: 'Acme Corp' })],
    }
    renderPage()
    fireEvent.click(screen.getByLabelText('Select client Acme Corp'))
    fireEvent.click(await screen.findByRole('button', { name: /Deactivate 1/ }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Deactivate 1' }))
    await waitFor(() => {
      expect(deletedId).toBe('c-1')
    })
  })
})
