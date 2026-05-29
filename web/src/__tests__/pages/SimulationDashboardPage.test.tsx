import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Capabilities } from '@/api/types/capabilities'

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
})
