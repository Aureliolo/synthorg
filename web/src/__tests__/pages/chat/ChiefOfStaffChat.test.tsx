import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { listAlerts, listProposals } from '@/api/endpoints/meta'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import { ChiefOfStaffChat } from '@/pages/chat/ChiefOfStaffChat'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

beforeEach(() => {
  useMetaStore.setState({
    chatLoading: false,
    error: null,
    proposals: [],
    alerts: [],
  })
})

describe('ChiefOfStaffChat', () => {
  it('renders the empty state before any message', () => {
    render(<ChiefOfStaffChat />)
    expect(screen.getByText('Ask the Chief of Staff')).toBeInTheDocument()
  })

  it('renders the question then the answer after sending', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(
          apiSuccess({
            answer: 'Signals look healthy this week.',
            sources: ['signal:revenue'],
            confidence: 0.9,
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<ChiefOfStaffChat />)

    await user.type(screen.getByLabelText('Chat message'), 'how are signals?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Signals look healthy this week.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('how are signals?')).toBeInTheDocument()
  })

  it('renders a failure notice when the chat request fails', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )
    const user = userEvent.setup()
    render(<ChiefOfStaffChat />)

    await user.type(screen.getByLabelText('Chat message'), 'anything')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText(/The assistant could not respond/)).toBeInTheDocument()
    })
    // The failure renders as a distinct error notice with a retry, not a reply.
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  describe('scope picker', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/meta/proposals', () =>
          HttpResponse.json(
            paginatedEnvelopeFor<typeof listProposals>([
              {
                id: 'prop-1',
                title: 'Tune retry backoff',
                action_type: 'signals.proposal',
                status: 'pending',
                risk_level: 'medium',
                requested_by: 'meta_improvement_service',
                created_at: '2026-06-20T12:00:00Z',
              },
            ]),
          ),
        ),
        http.get('/api/v1/meta/alerts', () =>
          HttpResponse.json(
            paginatedEnvelopeFor<typeof listAlerts>([
              {
                id: 'alert-1',
                severity: 'warning',
                alert_type: 'inflection',
                description: 'Quality dropped sharply',
                affected_domains: ['performance'],
                signal_context: {},
                recommended_action: null,
                emitted_at: '2026-06-20T12:05:00Z',
              },
            ]),
          ),
        ),
      )
    })

    it('does not render the picker before proposals/alerts load', () => {
      render(<ChiefOfStaffChat />)
      expect(
        screen.queryByLabelText('Scope to a proposal or alert (optional)'),
      ).not.toBeInTheDocument()
    })

    it('scopes the chat request to the selected proposal', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post('/api/v1/meta/chat', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json(
            apiSuccess({ answer: 'Explained.', sources: [], confidence: 0.8 }),
          )
        }),
      )
      const user = userEvent.setup()
      render(<ChiefOfStaffChat />)

      const picker = await screen.findByLabelText(
        'Scope to a proposal or alert (optional)',
      )
      await user.selectOptions(picker, 'proposal:prop-1')
      expect(screen.getByText(/Scoped to:/)).toBeInTheDocument()
      expect(screen.getByText('Tune retry backoff')).toBeInTheDocument()

      await user.type(screen.getByLabelText('Chat message'), 'why?')
      await user.click(screen.getByRole('button', { name: 'Send message' }))

      await waitFor(() => {
        expect(capturedBody).not.toBeNull()
      })
      expect(capturedBody).toMatchObject({
        proposal_id: 'prop-1',
        alert_id: null,
      })
    })

    it('scopes the chat request to the selected alert', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post('/api/v1/meta/chat', async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json(
            apiSuccess({ answer: 'Explained.', sources: [], confidence: 0.8 }),
          )
        }),
      )
      const user = userEvent.setup()
      render(<ChiefOfStaffChat />)

      const picker = await screen.findByLabelText(
        'Scope to a proposal or alert (optional)',
      )
      await user.selectOptions(picker, 'alert:alert-1')
      expect(screen.getByText(/Scoped to:/)).toBeInTheDocument()
      expect(screen.getByText('Quality dropped sharply')).toBeInTheDocument()

      await user.type(screen.getByLabelText('Chat message'), 'why?')
      await user.click(screen.getByRole('button', { name: 'Send message' }))

      await waitFor(() => {
        expect(capturedBody).not.toBeNull()
      })
      expect(capturedBody).toMatchObject({
        proposal_id: null,
        alert_id: 'alert-1',
      })
    })

    it('clears the scope when the chip close button is clicked', async () => {
      const user = userEvent.setup()
      render(<ChiefOfStaffChat />)

      const picker = await screen.findByLabelText(
        'Scope to a proposal or alert (optional)',
      )
      await user.selectOptions(picker, 'alert:alert-1')
      expect(screen.getByText(/Scoped to:/)).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Clear chat scope' }))
      expect(
        await screen.findByLabelText('Scope to a proposal or alert (optional)'),
      ).toBeInTheDocument()
    })
  })
})
