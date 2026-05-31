import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { MetaAct } from '@/pages/meta/MetaAct'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

const ROSTER = [
  { id: 'a-ceo', name: 'Dana', role: 'CEO' },
  { id: 'a-cfo', name: 'Casey', role: 'CFO' },
]

function _useRoster() {
  server.use(
    http.get('/api/v1/agents/active', () => HttpResponse.json(apiSuccess(ROSTER))),
  )
}

function _actResult(
  action: Record<string, unknown>,
): Record<string, unknown> {
  return {
    agent_id: 'a-cfo',
    agent_name: 'Casey',
    conversation_id: 'conv-act-1',
    action,
  }
}

beforeEach(() => {
  useMetaStore.setState({ actionLoading: false, error: null, activeAgents: [] })
})

describe('MetaAct', () => {
  it('renders the empty state with the active-agent picker', async () => {
    _useRoster()
    render(<MetaAct />)

    expect(screen.getByText('Direct an agent to act')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Casey/ })).toBeInTheDocument()
  })

  it('disables send until an agent is selected', async () => {
    _useRoster()
    const user = userEvent.setup()
    render(<MetaAct />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Casey/ })).toBeInTheDocument(),
    )

    await user.type(screen.getByLabelText('Instruction'), 'pull the numbers')
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /Casey/ }))
    expect(
      screen.getByRole('button', { name: 'Send message' }),
    ).not.toBeDisabled()
  })

  it('renders an executed-action card with the tool outcome', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/meta/chat/act', () =>
        HttpResponse.json(
          apiSuccess(
            _actResult({
              termination_reason: 'completed',
              final_message: 'Revenue is up 4%.',
              tool_calls: [
                { tool_name: 'query_metrics', is_error: false, result: 'ok' },
              ],
              approval_id: null,
              parked: false,
            }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<MetaAct />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Casey/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Casey/ }))
    await user.type(screen.getByLabelText('Instruction'), 'how is revenue?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText('Revenue is up 4%.')).toBeInTheDocument()
    })
    expect(screen.getByText('query_metrics')).toBeInTheDocument()
    // The human instruction is echoed into the transcript.
    expect(screen.getByText('how is revenue?')).toBeInTheDocument()
  })

  it('surfaces a parked action with a consent CTA to approvals', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/meta/chat/act', () =>
        HttpResponse.json(
          apiSuccess(
            _actResult({
              termination_reason: 'parked',
              final_message: null,
              tool_calls: [
                {
                  tool_name: 'request_human_approval',
                  is_error: true,
                  result: 'Approval required',
                },
              ],
              approval_id: 'appr-act-1',
              parked: true,
            }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MetaAct />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Casey/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Casey/ }))
    await user.type(screen.getByLabelText('Instruction'), 'deploy to prod')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText(/needs human approval before it runs/),
      ).toBeInTheDocument()
    })
    const link = screen.getByRole('link', { name: /Review in Approvals/ })
    expect(link).toHaveAttribute('href', '/approvals')
  })
})
