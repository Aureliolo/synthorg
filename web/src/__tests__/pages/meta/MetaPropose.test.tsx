import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { MetaPropose } from '@/pages/meta/MetaPropose'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

beforeEach(() => {
  useMetaStore.setState({ proposeLoading: false, error: null })
})

describe('MetaPropose', () => {
  it('renders the empty state before any turn', () => {
    render(<MetaPropose />)
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
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<MetaPropose />)

    await user.type(
      screen.getByLabelText('Work request'),
      'cut our cloud spend',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Queued 1 work item for your approval.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Trim cloud spend')).toBeInTheDocument()
    // Attribution: the routed CFO agent answered.
    expect(screen.getByText('Casey')).toBeInTheDocument()
    expect(screen.getByText('CFO')).toBeInTheDocument()
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
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<MetaPropose />)

    await user.type(screen.getByLabelText('Work request'), 'do a thing')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Which project is this for?'),
      ).toBeInTheDocument()
    })
  })
})
