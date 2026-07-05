import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { RequestWorkChat } from '@/pages/chat/RequestWorkChat'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

function renderChat() {
  // Queued proposals/steering render <Link> to /approvals, so a router is required.
  return render(
    <MemoryRouter>
      <RequestWorkChat />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useMetaStore.setState({ proposeLoading: false, error: null })
})

describe('RequestWorkChat', () => {
  it('renders the empty state before any turn', () => {
    renderChat()
    expect(screen.getByText('Request work')).toBeInTheDocument()
  })

  it('renders a routed proposal with role attribution after sending', async () => {
    server.use(
      http.post('/api/v1/meta/chat/propose', () =>
        HttpResponse.json(
          apiSuccess({
            conversation_id: 'conv-1',
            status: 'proposed',
            clarifying_question: null,
            conversation_closed: false,
            proposals: [
              {
                approval_id: 'a-1',
                proposal_id: 'p-1',
                title: 'Trim cloud spend',
                task_type: 'research',
                priority: 'high',
              },
            ],
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
        screen.getByText('Queued 1 item for your approval.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Trim cloud spend')).toBeInTheDocument()
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
            proposals: [],
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
      // A steering-only turn must still report the queued count, not "0".
      expect(
        screen.getByText('Queued 1 item for your approval.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Prioritise the launch checklist')).toBeInTheDocument()
    // The closed conversation disables further input.
    expect(screen.getByLabelText('Work request')).toBeDisabled()
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
            proposals: [],
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
