import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiError, buildSsrfViolation, emptyPageEnvelope, pageEnvelope, successFor } from '@/mocks/handlers'
import SsrfViolationsPage from '@/pages/security/SsrfViolationsPage'
import { useSsrfViolationsStore } from '@/stores/ssrf-violations'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'
import type { resolveSsrfViolation } from '@/api/endpoints/ssrf-violations'
import type { SsrfViolationDTO } from '@/api/types'

const authMock = vi.hoisted((): { userRole: string } => ({ userRole: 'ceo' }))
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ userRole: authMock.userRole }),
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <SsrfViolationsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  authMock.userRole = 'ceo'
  useSsrfViolationsStore.setState({ statusFilter: 'pending' })
  useToastStore.getState().dismissAll()
})

describe('SsrfViolationsPage', () => {
  it('renders blocked violations from the queue', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(pageEnvelope([buildSsrfViolation()])),
      ),
    )
    renderPage()
    expect(await screen.findByText('metadata.internal')).toBeInTheDocument()
    expect(screen.getByText('http://metadata.internal/latest/meta-data/')).toBeInTheDocument()
  })

  it('renders the empty state when there are no violations', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(emptyPageEnvelope<SsrfViolationDTO>()),
      ),
    )
    renderPage()
    expect(await screen.findByText('No SSRF violations')).toBeInTheDocument()
  })

  it('allows a pending violation through the confirmation dialog', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(pageEnvelope([buildSsrfViolation()])),
      ),
      http.post('/api/v1/providers/ssrf-violations/:id/resolve', ({ params }) =>
        HttpResponse.json(
          successFor<typeof resolveSsrfViolation>(
            buildSsrfViolation({ id: String(params['id']), status: 'allowed' }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('metadata.internal')
    await user.click(screen.getByRole('button', { name: /allow/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^allow$/i }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.variant === 'success')).toBe(true)
    })
  })

  it('surfaces an error toast and keeps the dialog open when resolve fails', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(pageEnvelope([buildSsrfViolation()])),
      ),
      http.post('/api/v1/providers/ssrf-violations/:id/resolve', () =>
        HttpResponse.json(apiError('forbidden'), { status: 403 }),
      ),
    )
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('metadata.internal')
    await user.click(screen.getByRole('button', { name: /allow/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^allow$/i }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.variant === 'error')).toBe(true)
    })
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

  it('renders the error banner with a retry when the queue fails to load', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Could not load SSRF violations')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('toasts and keeps the loaded list when loading more fails', async () => {
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        // First page advertises more; the follow-up page (with a cursor) 500s.
        if (cursor === null) {
          return HttpResponse.json(
            pageEnvelope([buildSsrfViolation()], { nextCursor: 'cursor-1' }),
          )
        }
        return HttpResponse.json(apiError('boom'), { status: 500 })
      }),
    )
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('metadata.internal')
    await user.click(screen.getByRole('button', { name: /load more/i }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.variant === 'error')).toBe(true)
    })
    // The already-loaded violation stays visible: a load-more failure must not
    // wipe the list or raise the page-level error banner.
    expect(screen.getByText('metadata.internal')).toBeInTheDocument()
    expect(screen.queryByText('Could not load SSRF violations')).not.toBeInTheDocument()
  })

  it('hides allow/deny actions for roles that cannot manage violations', async () => {
    authMock.userRole = 'developer'
    server.use(
      http.get('/api/v1/providers/ssrf-violations/', () =>
        HttpResponse.json(pageEnvelope([buildSsrfViolation()])),
      ),
    )
    renderPage()
    await screen.findByText('metadata.internal')
    expect(screen.queryByRole('button', { name: /allow/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /deny/i })).not.toBeInTheDocument()
  })
})
