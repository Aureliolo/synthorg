import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { ProvidersStep } from '@/pages/setup/ProvidersStep'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'
import { apiSuccess, apiError } from '@/mocks/handlers'

// The store-level happy / failure / warning paths for
// createProviderFromPreset are covered directly in
// `setup-wizard.test.ts`. This file targets the *page-level*
// `handleAddLocal` recovery flow specifically: the rendered
// `ProvidersStep` plus the warning-banner DOM check covers the
// integration that the lighter store tests can't reach.

function defaultProvider(name: string) {
  return {
    driver: 'litellm',
    litellm_provider: name,
    auth_type: 'none' as const,
    has_api_key: false,
    has_oauth_credentials: false,
    has_custom_header: false,
    has_subscription_token: false,
    tos_accepted_at: null,
    oauth_token_url: null,
    oauth_client_id: null,
    oauth_scope: null,
    custom_header_name: null,
    preset_name: null,
    supports_model_pull: false,
    supports_model_delete: false,
    supports_model_config: false,
    base_url: 'http://localhost:11434',
    models: [
      {
        id: `${name}-model`,
        alias: null,
        cost_per_1k_input: 0,
        cost_per_1k_output: 0,
        max_context: 4096,
        estimated_latency_ms: null,
        local_params: null,
      },
    ],
  }
}

function localPreset(name: string) {
  return {
    kind: 'local' as const,
    name,
    display_name: name,
    is_featured: true,
    candidate_urls: ['http://localhost:11434'],
  }
}

describe('ProvidersStep: providersWarning surface', () => {
  beforeEach(() => {
    useSetupWizardStore.getState().reset()
    useToastStore.getState().dismissAll()
    server.use(
      http.get('/api/v1/setup/personality-presets', () =>
        HttpResponse.json(apiSuccess({ presets: [] })),
      ),
      http.get('/api/v1/providers/presets', () =>
        HttpResponse.json(apiSuccess([localPreset('local-x')])),
      ),
      http.post('/api/v1/providers/probe-local', () =>
        HttpResponse.json(
          apiSuccess({
            results: {
              'local-x': {
                preset_name: 'local-x',
                reachable_url: 'http://localhost:11434',
                models: [],
                latency_ms: 12,
              },
            },
            errors: {},
          }),
        ),
      ),
    )
  })

  // The warning-banner branch is the unique value-add of this file:
  // a successful create + empty discovery should NOT render the
  // "Failed to load providers" error banner; it should render the
  // separate "Provider added with warnings" banner from the new
  // providersWarning slot.
  it('renders the warning banner (not the error banner) when discovery is empty after a successful create', async () => {
    const emptyProvider = { ...defaultProvider('local-x'), models: [] }
    server.use(
      http.post('/api/v1/providers/from-preset', () =>
        HttpResponse.json(apiSuccess(emptyProvider), { status: 201 }),
      ),
      // Use the literal `/local-x` path on the getProvider call so the
      // wildcard handler doesn't also match `/api/v1/providers/presets`
      // and break listPresets.
      http.post('/api/v1/providers/local-x/discover-models', () =>
        HttpResponse.json(
          apiSuccess({ discovered_models: [], provider_name: 'local-x' }),
        ),
      ),
      http.get('/api/v1/providers/local-x', () =>
        HttpResponse.json(apiSuccess(emptyProvider)),
      ),
    )

    render(<ProvidersStep />)
    await waitFor(() => {
      expect(useSetupWizardStore.getState().presets.length).toBeGreaterThan(0)
    })

    // Drive the store action the picker would invoke. Calling
    // createProviderFromPreset directly here (instead of clicking the
    // PresetPicker UI) is acceptable because the warning-banner
    // assertion is page-level: the store sets providersWarning, the
    // page reads it, and renders the banner. The picker click path
    // is exercised by Storybook visual tests.
    const result = await useSetupWizardStore
      .getState()
      .createProviderFromPreset('local-x', 'local-x', undefined, 'http://localhost:11434')

    expect(result.ok).toBe(true)
    expect(useSetupWizardStore.getState().providersError).toBeNull()
    expect(useSetupWizardStore.getState().providersWarning).toMatch(
      /no models were discovered/,
    )
    // Warning banner renders providersWarning, not providersError.
    expect(
      await screen.findByText(/Provider added with warnings/i),
    ).toBeInTheDocument()
    // And no error banner appears.
    expect(
      screen.queryByText(/Failed to load providers/i),
    ).not.toBeInTheDocument()
  })

  // The fetchProviders-after-create recovery flow (toast warning +
  // cleared providersError) is exercised at the store level by
  // ``setup-wizard.test.ts``: that file pins each branch of the
  // createProviderFromPreset return shape AND the fetchProviders
  // store action's error-recording behaviour. Adding a second
  // ProvidersStep render here would duplicate that coverage with no
  // additional value-add (the page-level integration is the same
  // call sequence, just with a render() wrapper) while paying the
  // async-leak cost of a second ProvidersStep mount.
})

// Suppress the apiError unused-import warning on the off chance
// future tests need it.
void apiError
