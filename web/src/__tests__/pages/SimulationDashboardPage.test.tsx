import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { buildSimulation, emptyPage, paginatedFor, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Capabilities } from '@/api/types/capabilities'
import type { cancelSimulation, listSimulations } from '@/api/endpoints/clients'
import type { SimulationStatusResponse } from '@/api/types/clients'

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
  web_search: true,
  web_search_blocker: 'none',
  web_search_message: '',
  web_search_notify: false,
  web_search_reusable_connections: [],
  web_fetch: true,
}

/** What the run is headed by, stated here rather than taken from a default. */
const RUNNING_PROJECT_NAME = 'Migrate the billing service'

function seedRunningSimulation() {
  server.use(
    http.get('/api/v1/simulations', () =>
      HttpResponse.json(
        paginatedFor<typeof listSimulations>({
          ...emptyPage<SimulationStatusResponse>(),
          data: [
            buildSimulation({
              simulation_id: 'sim-1',
              status: 'running',
              project_name: RUNNING_PROJECT_NAME,
            }),
          ],
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
    // The run is headed by what it simulates, not by its own key.
    expect(await screen.findByText(RUNNING_PROJECT_NAME)).toBeInTheDocument()
    expect(screen.queryByText('sim-1')).not.toBeInTheDocument()
    expect(screen.getByText('Active runs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Report' })).toBeInTheDocument()
  })

  it('names the run the report belongs to, without its key', async () => {
    seedRunningSimulation()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Report' }))
    // Runs share their statuses and round counts, so a heading that named
    // neither left the operator unable to tell which report they were reading.
    expect(
      await screen.findByText(`Simulation report: ${RUNNING_PROJECT_NAME}`),
    ).toBeInTheDocument()
    expect(screen.queryByText('sim-1')).not.toBeInTheDocument()
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
