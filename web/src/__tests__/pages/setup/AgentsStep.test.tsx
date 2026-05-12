import { fireEvent, render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { AgentsStep } from '@/pages/setup/AgentsStep'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { server } from '@/test-setup'
import { apiSuccess } from '@/mocks/handlers'
import type { SetupAgentSummary } from '@/api/types/setup'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

function agent(overrides: Partial<SetupAgentSummary> = {}): SetupAgentSummary {
  return {
    name: 'Alice Smith',
    role: 'Developer',
    department: 'engineering',
    level: 'mid',
    model_provider: 'cloud-x',
    model_id: 'cloud-x-large',
    tier: 'medium',
    personality_preset: null,
    ...overrides,
  }
}

function model(id: string, overrides: Partial<ProviderModelConfig> = {}): ProviderModelConfig {
  return {
    id,
    alias: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    max_context: 8192,
    estimated_latency_ms: null,
    local_params: null,
    ...overrides,
  }
}

function provider(name: string, models: ProviderModelConfig[]): ProviderConfig {
  // The full ProviderConfig type has 13 fields; tests only need the
  // ``models`` field for the unresolved-agent detection logic, so we
  // cast through ``unknown`` rather than synthesising every credential
  // indicator. If a future test exercises code that reads other
  // fields, add them here rather than expanding the cast.
  return {
    driver: 'litellm',
    litellm_provider: name,
    auth_type: 'api_key',
    base_url: null,
    models,
  } as unknown as ProviderConfig
}

describe('AgentsStep: unresolved-agent detection', () => {
  beforeEach(() => {
    useSetupWizardStore.getState().reset()
    // Personality presets endpoint always returns []; keeps fetchPresets quiet
    server.use(
      http.get('/api/v1/setup/personality-presets', () =>
        HttpResponse.json(apiSuccess({ presets: [] })),
      ),
    )
  })

  function findBanner(): HTMLElement {
    const titleEl = screen.getByText(/references a missing provider or model/i)
    // Walk up to the role=status container that wraps title + body + action.
    const banner = titleEl.closest('[role="status"]')
    if (!banner) {
      throw new Error('expected banner container with role="status"')
    }
    return banner as HTMLElement
  }

  it('shows the trapped-state banner when an agent references a missing provider', () => {
    useSetupWizardStore.setState({
      agents: [agent({ name: 'Alice', model_provider: 'gone', model_id: 'm-1' })],
      providers: { 'cloud-x': provider('cloud-x', [model('cloud-x-large')]) },
      // Explicit starting step so the click+state assertion below
      // verifies a real navigation. Without this, the click could
      // pass trivially because the wizard might already be on
      // ``'providers'`` from an earlier test that didn't reset it.
      currentStep: 'agents',
    })

    render(<AgentsStep />)

    const banner = findBanner()
    expect(within(banner).getByText(/Alice/)).toBeInTheDocument()
    const action = within(banner).getByRole('button', {
      name: /Open Providers step/i,
    })
    expect(action).toBeInTheDocument()

    // Clicking the action moves the wizard back to the providers step.
    // Without this assertion, a future refactor could swap the
    // ``onClick`` to a no-op and the test would still pass.
    fireEvent.click(action)
    expect(useSetupWizardStore.getState().currentStep).toBe('providers')
  })

  it('shows the banner when an agent references a missing model on a configured provider', () => {
    useSetupWizardStore.setState({
      agents: [agent({ name: 'Bob', model_provider: 'cloud-x', model_id: 'gone' })],
      providers: { 'cloud-x': provider('cloud-x', [model('cloud-x-large')]) },
    })

    render(<AgentsStep />)

    const banner = findBanner()
    expect(within(banner).getByText(/Bob/)).toBeInTheDocument()
    expect(within(banner).getByText(/no model 'gone'/i)).toBeInTheDocument()
  })

  it('shows the banner for agents with no model assigned at all', () => {
    useSetupWizardStore.setState({
      agents: [agent({ name: 'Charlie', model_provider: null, model_id: null })],
      providers: { 'cloud-x': provider('cloud-x', [model('cloud-x-large')]) },
    })

    render(<AgentsStep />)

    const banner = findBanner()
    expect(within(banner).getByText(/Charlie/)).toBeInTheDocument()
    expect(within(banner).getByText(/no model assigned/i)).toBeInTheDocument()
  })

  it('hides the banner when every agent resolves cleanly', () => {
    useSetupWizardStore.setState({
      agents: [agent({ name: 'Alice', model_provider: 'cloud-x', model_id: 'cloud-x-large' })],
      providers: { 'cloud-x': provider('cloud-x', [model('cloud-x-large')]) },
    })

    render(<AgentsStep />)

    expect(
      screen.queryByText(/references a missing provider or model/i),
    ).not.toBeInTheDocument()
  })
})
