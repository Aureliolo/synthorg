import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'

import { WebResearchBanner } from '@/components/layout/WebResearchBanner'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { getCapabilities } from '@/api/endpoints/capabilities'
import type { Capabilities } from '@/api/types/capabilities'

/**
 * Web search enabled and answering nothing has to reach the operator: it reads
 * as on everywhere else in the dashboard, so without this banner the only
 * symptom is an agent quietly working from stale priors.
 *
 * Dismissal is a backend write rather than a client flag (the dashboard
 * persists no state of its own), so these assert the PUT as well as the
 * disappearance it causes.
 */

const READY: Capabilities = {
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

const NO_PROVIDER: Capabilities = {
  ...READY,
  web_search: false,
  web_search_blocker: 'no_provider',
  web_search_message: 'Web search is enabled but no provider is selected.',
  web_search_notify: true,
}

function serveCapabilities(matrix: Capabilities) {
  server.use(
    http.get('/api/v1/capabilities/', () =>
      HttpResponse.json(successFor<typeof getCapabilities>(matrix)),
    ),
  )
}

function renderBanner() {
  return render(
    <MemoryRouter>
      <WebResearchBanner />
    </MemoryRouter>,
  )
}

describe('WebResearchBanner', () => {
  it('surfaces the backend blocker message when search is unusable', async () => {
    serveCapabilities(NO_PROVIDER)
    renderBanner()
    expect(await screen.findByText('Web search is not configured')).toBeInTheDocument()
    expect(
      screen.getByText(/no provider is selected/i),
    ).toBeInTheDocument()
  })

  it('stays silent when search is configured', async () => {
    serveCapabilities(READY)
    renderBanner()
    await waitFor(() => {
      expect(screen.queryByText('Web search is not configured')).not.toBeInTheDocument()
    })
  })

  it('stays silent once the notice is dismissed, even though search is still blocked', async () => {
    serveCapabilities({ ...NO_PROVIDER, web_search_notify: false })
    renderBanner()
    await waitFor(() => {
      expect(screen.queryByText('Web search is not configured')).not.toBeInTheDocument()
    })
  })

  it('names a saved connection for the selected provider instead of asking again', async () => {
    serveCapabilities({
      ...NO_PROVIDER,
      web_search_blocker: 'no_connection',
      web_search_message: 'Web search is enabled but no connection is bound.',
      web_search_reusable_connections: ['my-search-key'],
    })
    renderBanner()
    expect(
      await screen.findByText(/You already have a connection for this provider \(my-search-key\)/),
    ).toBeInTheDocument()
  })

  it('asks the operator to sign up when nothing reusable exists', async () => {
    serveCapabilities(NO_PROVIDER)
    renderBanner()
    expect(
      await screen.findByText(/Every provider needs an account of your own/),
    ).toBeInTheDocument()
  })

  it('writes the dismissal to the backend and stops showing the notice', async () => {
    serveCapabilities(NO_PROVIDER)
    let written: unknown = null
    server.use(
      http.put('/api/v1/settings/tools/web_search_notice_dismissed', async ({ request }) => {
        written = await request.json()
        // The banner re-reads the matrix after the write, so the endpoint has
        // to start answering as dismissed or the notice would never clear.
        serveCapabilities({ ...NO_PROVIDER, web_search_notify: false })
        return HttpResponse.json(
          successFor<() => Promise<unknown>>({
            namespace: 'tools',
            key: 'web_search_notice_dismissed',
            value: 'true',
          }),
        )
      }),
    )
    renderBanner()
    const dismiss = await screen.findByRole('button', { name: 'Dismiss' })
    await userEvent.click(dismiss)
    await waitFor(() => {
      expect(screen.queryByText('Web search is not configured')).not.toBeInTheDocument()
    })
    expect(written).toEqual({ value: 'true' })
  })
})
