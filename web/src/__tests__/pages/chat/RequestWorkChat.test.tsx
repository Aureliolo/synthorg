import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { RequestWorkChat } from '@/pages/chat/RequestWorkChat'
import { useMetaStore } from '@/stores/meta'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

function renderChat() {
  // The plan-draft link (/plans) and steering links (/approvals) render
  // <Link>, so a router is required.
  return render(
    <MemoryRouter>
      <RequestWorkChat />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useMetaStore.setState({ proposeLoading: false, error: null })
  useToastStore.setState({ toasts: [] })
})

describe('RequestWorkChat', () => {
  it('renders the empty state before any turn', () => {
    renderChat()
    expect(screen.getByText('Request work')).toBeInTheDocument()
  })

  it('renders a drafted plan with role attribution after sending', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-1',
            status: 'proposed',
            clarifying_question: null,
            conversation_closed: false,
            plan_draft: {
              task_id: 'task-1',
              project: 'Cost',
              title: 'Trim cloud spend',
            },
            responder_role: 'CFO',
            responder_name: 'Casey',
            routed_topic: 'budget',
            routing_confidence: 0.9,
            steering: [],
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    renderChat()

    await user.type(
      screen.getByLabelText('Work request'),
      'cut our cloud spend',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Drafted a plan for your review.'),
      ).toBeInTheDocument()
    })
    // The plan title links into Plan Review for holistic review.
    const planLink = screen.getByRole('link', { name: 'Trim cloud spend' })
    expect(planLink).toHaveAttribute('href', '/plans')
    // Attribution: the routed CFO agent answered.
    expect(screen.getByText('Casey')).toBeInTheDocument()
    expect(screen.getByText('CFO')).toBeInTheDocument()
  })

  it('counts steering directives and closes the conversation', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-3',
            status: 'proposed',
            clarifying_question: null,
            conversation_closed: true,
            plan_draft: null,
            responder_role: null,
            responder_name: null,
            routed_topic: null,
            routing_confidence: null,
            steering: [
              {
                approval_id: 's-1',
                kind: 'redirect',
                project: 'launch',
                text: 'Prioritise the launch checklist',
              },
            ],
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('Work request'), 'steer the team')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      // A steering-only turn (no plan drafted) reports the steering count.
      expect(
        screen.getByText('Queued 1 steering directive for your confirmation.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Prioritise the launch checklist')).toBeInTheDocument()
    // The closed conversation disables further input.
    expect(screen.getByLabelText('Work request')).toBeDisabled()
  })

  it('cancels an in-flight propose and shows the cancelled notice (no error toast)', async () => {
    // Never resolves server-side (a bare pending promise, so no timer / leaked
    // handle); the request only settles via the client abort the Cancel button
    // triggers.
    server.use(
      http.post('/api/v1/meta/chat/propose', async () => {
        await new Promise<void>(() => {})
        return HttpResponse.json(apiSuccess({}))
      }),
    )
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('Work request'), 'do a slow thing')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    // The Cancel affordance appears while the propose is in flight.
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => {
      expect(screen.getByText(/request cancelled/i)).toBeInTheDocument()
    })
    // A deliberate cancel is not an error: no failure notice, no error toast.
    expect(screen.queryByText(/could not respond/i)).not.toBeInTheDocument()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('renders a clarifying question without attribution when unrouted', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-2',
            status: 'needs_clarification',
            clarifying_question: 'Which project is this for?',
            conversation_closed: false,
            plan_draft: null,
            responder_role: null,
            responder_name: null,
            routed_topic: null,
            routing_confidence: null,
            steering: [],
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('Work request'), 'do a thing')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Which project is this for?'),
      ).toBeInTheDocument()
    })
  })
})
