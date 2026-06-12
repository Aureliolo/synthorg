import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { buildSimulation, emptyPage, paginatedFor, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Capabilities } from '@/api/types/capabilities'
import type {
  cancelSimulation,
  listSimulations,
  SimulationStatusResponse,
} from '@/api/endpoints/clients'

interface CapReturn {
  capabilities: Capabilities
  loading: boolean
  error: string | null
}

let capReturn: CapReturn

vi.mock('@/hooks/useCapabilities', () => ({
  useCapabilities: () => capReturn,
}))

const { default: SimulationDashboardPage } = await import('@/pages/SimulationDashboardPage')

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

function seedRunningSimulation() {
  server.use(
    http.get('/api/v1/simulations', () =>
      HttpResponse.json(
        paginatedFor<typeof listSimulations>({
          ...emptyPage<SimulationStatusResponse>(),
          data: [buildSimulation({ simulation_id: 'sim-1', status: 'running' })],
        }),
      ),
    ),
  )
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SimulationDashboardPage />
    </MemoryRouter>,
  )
}

describe('SimulationDashboardPage', () => {
  beforeEach(() => {
    capReturn = { capabilities: ALL_ENABLED, loading: false, error: null }
    vi.clearAllMocks()
  })

  it('renders the cap-error fallback when capability detection fails', () => {
    capReturn = { capabilities: ALL_ENABLED, loading: false, error: 'capability fetch failed' }
    renderPage()
    expect(screen.getByText('Could not determine available features')).toBeInTheDocument()
    expect(screen.getByText('capability fetch failed')).toBeInTheDocument()
  })

  it('renders the not-configured fallback when simulations are disabled', () => {
    capReturn = { capabilities: { ...ALL_ENABLED, simulations: false }, loading: false, error: null }
    renderPage()
    expect(screen.getByText('Simulations not configured')).toBeInTheDocument()
  })

  it('renders the loading fallback (not the not-configured copy) while capabilities resolve', () => {
    capReturn = { capabilities: ALL_ENABLED, loading: true, error: null }
    renderPage()
    expect(screen.queryByText('Simulations not configured')).not.toBeInTheDocument()
    expect(screen.queryByText('No simulation runs yet')).not.toBeInTheDocument()
  })

  it('renders the metrics + empty-runs state once enabled and the fetch returns no runs', async () => {
    // The default /simulations handler returns an empty page.
    renderPage()
    expect(await screen.findByText('No simulation runs yet')).toBeInTheDocument()
    expect(screen.getByText('Active runs')).toBeInTheDocument()
  })

  it('renders run cards and active-run metrics when simulations load', async () => {
    seedRunningSimulation()
    renderPage()
    expect(await screen.findByText('sim-1')).toBeInTheDocument()
    expect(screen.getByText('Active runs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Report' })).toBeInTheDocument()
  })

  it('shows the report card when Report is clicked', async () => {
    seedRunningSimulation()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Report' }))
    expect(await screen.findByText('Report: sim-1')).toBeInTheDocument()
  })

  it('cancels a running simulation and reflects the cancelled status', async () => {
    let cancelled = false
    server.use(
      http.post('/api/v1/simulations/:id/cancel', ({ params }) => {
        cancelled = true
        return HttpResponse.json(
          successFor<typeof cancelSimulation>(
            buildSimulation({ simulation_id: String(params['id']), status: 'cancelled' }),
          ),
        )
      }),
      http.get('/api/v1/simulations', () =>
        HttpResponse.json(
          paginatedFor<typeof listSimulations>({
            ...emptyPage<SimulationStatusResponse>(),
            data: [
              buildSimulation({
                simulation_id: 'sim-1',
                status: cancelled ? 'cancelled' : 'running',
              }),
            ],
          }),
        ),
      ),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument(),
    )
    expect(screen.getByText('cancelled')).toBeInTheDocument()
  })
})
