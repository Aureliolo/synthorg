/**
 * The Providers page over its real data chain, not a mocked hook.
 *
 * `ProvidersPage.test.tsx` mocks `useProvidersData` away entirely, so nothing
 * exercised `ProvidersPage -> useProvidersData -> usePolling -> fetchProviders
 * -> listProviders -> GET /api/v1/providers`. That left the page's whole
 * reason for existing untested: a break anywhere along it renders exactly the
 * same "No providers configured" empty state as a genuinely empty install, so
 * the failure mode is invisible rather than loud.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import ProvidersPage from '@/pages/ProvidersPage'
import { server } from '@/test-setup'
import type { getProviderHealth, listProviders } from '@/api/endpoints/providers'
import type { ProviderConfig, ProviderHealthSummary } from '@/api/types/providers'
import { paginatedEnvelopeFor, successFor } from '@/mocks/handlers/helpers'

function provider(name: string): ProviderConfig {
  return {
    driver: 'litellm',
    litellm_provider: 'test',
    auth_type: 'api_key',
    base_url: null,
    keep_alive: null,
    models: [],
    has_api_key: true,
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
    agent_eligible: true,
    billing_model: 'per_token',
    supports_model_config: false,
    name,
  }
}

const HEALTH: ProviderHealthSummary = {
  last_check_timestamp: '2026-08-13T12:00:00Z',
  avg_response_time_ms: 120,
  error_rate_percent_24h: 0,
  calls_last_24h: 3,
  health_status: 'up',
  liveness_calls: 3,
  liveness_error_rate_percent: 0,
  total_tokens_24h: 0,
  total_cost_24h: 0,
}

function renderPage(): void {
  render(
    <MemoryRouter>
      <ProvidersPage />
    </MemoryRouter>,
  )
}

describe('ProvidersPage over its real fetch chain', () => {
  it('renders the providers the API returns', async () => {
    server.use(
      http.get('/api/v1/providers', () =>
        HttpResponse.json(
          paginatedEnvelopeFor<typeof listProviders>([
            provider('example-provider'),
            provider('test-provider'),
          ]),
        ),
      ),
      http.get('/api/v1/providers/:name/health', () =>
        HttpResponse.json(successFor<typeof getProviderHealth>(HEALTH)),
      ),
    )

    renderPage()

    // Queried by accessible name rather than raw text: the card splits the
    // provider name across nodes, so a text match would assert on markup
    // rather than on the provider having arrived.
    await waitFor(() => {
      expect(
        screen.getByLabelText('Select provider example-provider'),
      ).toBeInTheDocument()
    })
    expect(
      screen.getByLabelText('Select provider test-provider'),
    ).toBeInTheDocument()
    // The empty state and a broken fetch are indistinguishable to the eye,
    // so assert the empty state is gone rather than only that names appeared.
    expect(screen.queryByText('No providers configured')).not.toBeInTheDocument()
  })

  it('shows the empty state when the API genuinely returns none', async () => {
    server.use(
      http.get('/api/v1/providers', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listProviders>([])),
      ),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No providers configured')).toBeInTheDocument()
    })
  })
})
