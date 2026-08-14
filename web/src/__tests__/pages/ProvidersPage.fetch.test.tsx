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
import type { ProviderHealthSummary } from '@/api/types/providers'
import { buildProvider } from '@/mocks/handlers/providers/crud'
import { paginatedEnvelopeFor, successFor } from '@/mocks/handlers/helpers'

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
            buildProvider({ name: 'example-provider' }),
            buildProvider({ name: 'test-provider' }),
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

  it('shows the skeleton until the first response arrives', async () => {
    // The third thing the list area can be, and the only one the other cases
    // cannot reach: with the request in flight the page must not yet claim
    // the install has no providers, which is the same wrong answer a failed
    // fetch used to give.
    let release = (): void => undefined
    const inFlight = new Promise<void>((resolve) => {
      release = () => {
        resolve()
      }
    })
    server.use(
      http.get('/api/v1/providers', async () => {
        await inFlight
        return HttpResponse.json(paginatedEnvelopeFor<typeof listProviders>([]))
      }),
    )

    renderPage()

    expect(screen.getByLabelText('Loading providers')).toBeInTheDocument()
    expect(screen.queryByText('No providers configured')).not.toBeInTheDocument()

    // Released rather than left pending: an unresolved request outlives the
    // test and the active-handle gate fails on it.
    release()
    await waitFor(() => {
      expect(screen.getByText('No providers configured')).toBeInTheDocument()
    })
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

  it('says the list could not load rather than showing it as empty', async () => {
    // The regression this file exists for: a failed fetch that renders the
    // empty state is indistinguishable from a genuinely empty install, so the
    // operator reads "you have configured nothing" when the truth is "we
    // could not ask".
    server.use(
      http.get('/api/v1/providers', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Could not load providers')).toBeInTheDocument()
    })
    expect(screen.queryByText('No providers configured')).not.toBeInTheDocument()
  })
})
