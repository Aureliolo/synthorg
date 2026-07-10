import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { listAlerts, listProposals } from '@/api/endpoints/meta'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import { sseFrame, sseStream } from '@/mocks/handlers/meta'
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

  it('streams the answer token-by-token after sending', async () => {
    // An unscoped question takes the streaming path (/meta/chat/stream);
    // deltas assemble into the answer and the complete frame carries the
    // sources / confidence.
    server.use(
      http.post('/api/v1/meta/chat/stream', () =>
        sseStream([
          sseFrame('progress', { delta: 'Signals ' }),
          sseFrame('progress', { delta: 'look healthy this week.' }),
          sseFrame('complete', {
            answer: 'Signals look healthy this week.',
            sources: ['signal:revenue'],
            confidence: 0.9,
          }),
        ]),
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
    // The model's confidence is surfaced alongside the answer.
    expect(screen.getByText('Confidence: 90%')).toBeInTheDocument()
  })

  it('surfaces cited org-state records as reference chips', async () => {
    server.use(
      http.post('/api/v1/meta/chat/stream', () =>
        sseStream([
          sseFrame('progress', { delta: 'Working on the platform revamp.' }),
          sseFrame('complete', {
            answer: 'Working on the platform revamp.',
            sources: ['tasks', 'projects'],
            cited_records: [
              {
                kind: 'task',
                record_id: 'task-1',
                label: 'Fix login',
                status: 'in_review',
              },
              {
                kind: 'project',
                record_id: 'proj-1',
                label: 'Platform Revamp',
                status: 'active',
              },
            ],
            confidence: 0.9,
          }),
        ]),
      ),
    )
    const user = userEvent.setup()
    render(<ChiefOfStaffChat />)

    await user.type(
      screen.getByLabelText('Chat message'),
      'what is the org working on?',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText('Fix login')).toBeInTheDocument()
    })
    expect(screen.getByText('Platform Revamp')).toBeInTheDocument()
    expect(screen.getByText('(in_review)')).toBeInTheDocument()
  })

  it('keeps the partial answer and stops streaming when Stop is clicked', async () => {
    // A stream that emits one delta then never completes, so the turn stays
    // in flight until the client aborts it.
    server.use(
      http.post('/api/v1/meta/chat/stream', ({ request }) => {
        const encoder = new TextEncoder()
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(sseFrame('progress', { delta: 'Partial ' })))
            // Error the stream when the client aborts so the reader rejects,
            // mirroring how a real fetch tears down on AbortController.abort().
            request.signal.addEventListener('abort', () => {
              try {
                controller.error(new DOMException('Aborted', 'AbortError'))
              } catch {
                /* already closed */
              }
            })
          },
        })
        return new HttpResponse(body, {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }),
    )
    const user = userEvent.setup()
    render(<ChiefOfStaffChat />)

    await user.type(screen.getByLabelText('Chat message'), 'stream please')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => {
      expect(screen.getByText('Partial')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /stop/i }))
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /stop/i }),
      ).not.toBeInTheDocument()
    })
    // The partial answer survives the abort; it is not discarded.
    expect(screen.getByText(/Partial/)).toBeInTheDocument()
  })

  it('renders a failure notice when the stream fails', async () => {
    server.use(
      http.post(
        '/api/v1/meta/chat/stream',
        () => new HttpResponse(null, { status: 500 }),
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

  it('reuses the idempotency key when retrying a failed scoped turn', async () => {
    // Scoped questions take the buffered /meta/chat path, which carries the
    // Idempotency-Key; a manual retry must reuse it so a turn that actually
    // succeeded server-side is deduped rather than re-run. (The unscoped
    // streaming path is intentionally key-less: a failed stream never
    // completed, so there is nothing to dedupe.)
    server.use(
      http.get('/api/v1/meta/proposals', () =>
        HttpResponse.json(
          paginatedEnvelopeFor<typeof listProposals>([
            {
              id: 'prop-9',
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
    )
    const keys: (string | null)[] = []
    let call = 0
    server.use(
      http.post('/api/v1/meta/chat', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        call += 1
        return call === 1
          ? HttpResponse.json(apiError('boom'))
          : HttpResponse.json(
              apiSuccess({ answer: 'Recovered.', sources: [], confidence: 0.7 }),
            )
      }),
    )
    const user = userEvent.setup()
    render(<ChiefOfStaffChat />)

    const picker = await screen.findByLabelText(
      'Scope to a proposal or alert (optional)',
    )
    await user.selectOptions(picker, 'proposal:prop-9')

    await user.type(screen.getByLabelText('Chat message'), 'try me')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /try again/i }))
    await waitFor(() => {
      expect(screen.getByText('Recovered.')).toBeInTheDocument()
    })

    expect(keys).toHaveLength(2)
    expect(keys[0]).not.toBeNull()
    expect(keys[0]).toBe(keys[1])
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
