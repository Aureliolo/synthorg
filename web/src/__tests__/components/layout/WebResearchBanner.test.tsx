import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'

import { WebResearchBanner } from '@/components/layout/WebResearchBanner'
import { successFor } from '@/mocks/handlers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import { server } from '@/test-setup'
import type { getCapabilities } from '@/api/endpoints/capabilities'
import type { updateSetting } from '@/api/endpoints/settings'
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

/**
 * Serve `matrix`, exposing a `requested` promise that settles once it was read.
 *
 * Asserting a banner is ABSENT is satisfied by a page that has not rendered
 * yet, so an absence test with nothing to wait on passes before the capability
 * request has even been issued: it would go on passing if the component
 * stopped fetching altogether. Awaiting `requested` first makes the absence a
 * statement about a matrix that arrived. Wrapped in an object so the tests
 * asserting a PRESENT banner (already gated on finding it) can ignore the
 * handle without each having to disclaim a promise they do not need.
 */
function serveCapabilities(matrix: Capabilities): { requested: Promise<void> } {
  let seen: () => void = () => undefined
  const requested = new Promise<void>((resolve) => {
    seen = resolve
  })
  server.use(
    http.get('/api/v1/capabilities/', () => {
      seen()
      return HttpResponse.json(successFor<typeof getCapabilities>(matrix))
    }),
  )
  return { requested }
}

function renderBanner() {
  return render(
    <MemoryRouter>
      <WebResearchBanner />
    </MemoryRouter>,
  )
}

/**
 * The entry the settings API really returns for the dismissal write.
 *
 * Built through the shared factory rather than hand-rolled: the store reads
 * ``definition.namespace``, so a flat stand-in makes the write fail while the
 * test still appears to pass.
 */
function _dismissedEntry() {
  return buildSettingEntry({
    value: 'true',
    definition: { namespace: 'tools', key: 'web_search_notice_dismissed' },
  })
}

describe('WebResearchBanner', () => {
  it('surfaces the backend blocker message when search is unusable', async () => {
    serveCapabilities(NO_PROVIDER)
    renderBanner()
    expect(await screen.findByText('Web search is not configured')).toBeInTheDocument()
    expect(screen.getByText(/no provider is selected/i)).toBeInTheDocument()
  })

  it('stays silent when search is configured', async () => {
    const requested = serveCapabilities(READY)
    renderBanner()
    await requested.requested
    await waitFor(() => {
      expect(screen.queryByText('Web search is not configured')).not.toBeInTheDocument()
    })
  })

  it('stays silent once the notice is dismissed, even though search is still blocked', async () => {
    const requested = serveCapabilities({ ...NO_PROVIDER, web_search_notify: false })
    renderBanner()
    await requested.requested
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

  it('links to the settings rows that clear the blocker', async () => {
    serveCapabilities(NO_PROVIDER)
    renderBanner()
    const link = await screen.findByRole('link', { name: 'Open web search settings' })
    expect(link).toHaveAttribute('href', '/settings/tools?q=web_search')
  })

  it('writes the dismissal to the backend and stops showing the notice', async () => {
    serveCapabilities(NO_PROVIDER)
    let written: unknown = null
    server.use(
      http.put('/api/v1/settings/tools/web_search_notice_dismissed', async ({ request }) => {
        written = await request.json()
        // The store re-reads the matrix after a capability-bearing write, so
        // the endpoint has to start answering as dismissed or the notice would
        // never clear.
        serveCapabilities({ ...NO_PROVIDER, web_search_notify: false })
        return HttpResponse.json(successFor<typeof updateSetting>(_dismissedEntry()))
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

  it('keeps the Dismiss button mounted while the write is in flight', async () => {
    // Unmounting it mid-request throws a keyboard user's focus to the document
    // body, so the button stays and disables instead.
    //
    // The write is held open by a promise the test resolves, not a timer: the
    // active-handle gate fails any test that leaves one behind, and a deferred
    // also makes the in-flight window exact rather than a race against a delay.
    serveCapabilities(NO_PROVIDER)
    let release: () => void = () => undefined
    const inFlight = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.put('/api/v1/settings/tools/web_search_notice_dismissed', async () => {
        await inFlight
        return HttpResponse.json(successFor<typeof updateSetting>(_dismissedEntry()))
      }),
    )
    renderBanner()
    const dismiss = await screen.findByRole('button', { name: 'Dismiss' })
    await userEvent.click(dismiss)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDisabled()
    })
    release()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Dismiss' })).toBeEnabled()
    })
  })

  it('stays silent when the capability read fails', async () => {
    // A matrix that never arrived is not evidence of a misconfiguration.
    server.use(
      http.get('/api/v1/capabilities/', () => HttpResponse.json({}, { status: 500 })),
    )
    renderBanner()
    await waitFor(() => {
      expect(screen.queryByText('Web search is not configured')).not.toBeInTheDocument()
    })
  })
})
