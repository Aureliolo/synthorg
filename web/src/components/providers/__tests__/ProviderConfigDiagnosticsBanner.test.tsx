import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { apiSuccess } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'
import { ProviderConfigDiagnosticsBanner } from '../ProviderConfigDiagnosticsBanner'
import type { ProviderConfigDiagnostics } from '@/api/types/providers'

function serveDiagnostics(data: ProviderConfigDiagnostics): void {
  server.use(
    http.get('/api/v1/providers/config-diagnostics', () =>
      HttpResponse.json(apiSuccess(data)),
    ),
  )
}

describe('ProviderConfigDiagnosticsBanner', () => {
  it('says nothing when the persisted config reads cleanly', async () => {
    serveDiagnostics({ status: 'ok', rejected: [], coerced: [], detail: null })

    const { container } = render(<ProviderConfigDiagnosticsBanner />)

    // Settle the fetch before asserting, or an empty DOM would pass while
    // the request is still in flight and prove nothing.
    await waitFor(() => {
      expect(container.querySelector('[role="alert"], [role="status"]')).toBeNull()
    })
  })

  it('names the connections that could not be read', async () => {
    serveDiagnostics({
      status: 'partial',
      rejected: [{ name: 'example-local', reason: 'driver: too short' }],
      coerced: [],
      detail: null,
    })

    render(<ProviderConfigDiagnosticsBanner />)

    expect(
      await screen.findByText(/Some provider connections could not be read/),
    ).toBeInTheDocument()
    expect(await screen.findByText(/example-local/)).toBeInTheDocument()
  })

  it('says an unreadable config is not an unconfigured company', async () => {
    serveDiagnostics({
      status: 'unreadable',
      rejected: [],
      coerced: [],
      detail: 'schema_version: Field required',
    })

    render(<ProviderConfigDiagnosticsBanner />)

    expect(
      await screen.findByText(/Provider configuration could not be read/),
    ).toBeInTheDocument()
    expect(await screen.findByText(/not an unconfigured company/)).toBeInTheDocument()
    expect(await screen.findByText(/schema_version/)).toBeInTheDocument()
  })

  it('escalates an unreadable config to an assertive alert', async () => {
    serveDiagnostics({
      status: 'unreadable',
      rejected: [],
      coerced: [],
      detail: 'schema_version: Field required',
    })

    render(<ProviderConfigDiagnosticsBanner />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('stays silent when the diagnostics read itself fails', async () => {
    // A banner about the banner is noise on a page already reporting a
    // problem, so a failed read renders nothing rather than an error.
    server.use(
      http.get('/api/v1/providers/config-diagnostics', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )

    const { container } = render(<ProviderConfigDiagnosticsBanner />)

    await waitFor(() => {
      expect(container.querySelector('[role="alert"], [role="status"]')).toBeNull()
    })
  })
})
