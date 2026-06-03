import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { MetaGroup } from '@/pages/meta/MetaGroup'
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

function _roundResult(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    conversation_id: 'conv-grp-1',
    contributions: [
      {
        agent_id: 'a-ceo',
        agent_name: 'Dana',
        participant_role: 'CEO',
        content: 'Prioritise enterprise.',
        sequence: 1,
        input_tokens: 80,
        output_tokens: 30,
      },
      {
        agent_id: 'a-cfo',
        agent_name: 'Casey',
        participant_role: 'CFO',
        content: 'It needs a bigger budget.',
        sequence: 2,
        input_tokens: 90,
        output_tokens: 28,
      },
    ],
    participants: [
      {
        id: 'p-ceo',
        conversation_id: 'conv-grp-1',
        agent_id: 'a-ceo',
        agent_name: 'Dana',
        participant_role: 'CEO',
        status: 'active',
        added_by: 'user-1',
        added_at: '2026-05-19T09:00:00Z',
      },
      {
        id: 'p-cfo',
        conversation_id: 'conv-grp-1',
        agent_id: 'a-cfo',
        agent_name: 'Casey',
        participant_role: 'CFO',
        status: 'active',
        added_by: 'user-1',
        added_at: '2026-05-19T09:00:00.000001Z',
      },
    ],
    participants_skipped: [],
    truncated_reason: null,
    pending_invites: [],
    ...overrides,
  }
}

beforeEach(() => {
  useMetaStore.setState({ groupChatLoading: false, error: null, activeAgents: [] })
})

describe('MetaGroup', () => {
  it('renders the start state with the active-agent picker', async () => {
    _useRoster()
    render(<MetaGroup />)

    expect(screen.getByText('Start a group chat')).toBeInTheDocument()
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Dana/ }),
      ).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Casey/ })).toBeInTheDocument()
  })

  it('disables send until at least one agent is selected', async () => {
    _useRoster()
    const user = userEvent.setup()
    render(<MetaGroup />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument(),
    )

    await user.type(screen.getByLabelText('Message'), 'hello team')
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /Dana/ }))
    expect(
      screen.getByRole('button', { name: 'Send message' }),
    ).not.toBeDisabled()
  })

  it('runs a round and renders attributed contributions + roster', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(apiSuccess(_roundResult())),
      ),
    )
    const user = userEvent.setup()
    render(<MetaGroup />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Dana/ }))
    await user.click(screen.getByRole('button', { name: /Casey/ }))
    await user.type(screen.getByLabelText('Message'), 'should we move upmarket?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText('Prioritise enterprise.')).toBeInTheDocument()
    })
    expect(screen.getByText('It needs a bigger budget.')).toBeInTheDocument()
    // Both agents are attributed (bubble attribution + roster strip).
    expect(screen.getAllByText('Dana').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Casey').length).toBeGreaterThan(0)
    // The human turn is echoed into the transcript.
    expect(screen.getByText('should we move upmarket?')).toBeInTheDocument()
  })

  it('surfaces a pending invite with a consent CTA to approvals', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(
          apiSuccess(
            _roundResult({
              pending_invites: [
                {
                  approval_id: 'appr-inv-1',
                  requested_by_agent_id: 'a-ceo',
                  requested_by_name: 'Dana',
                  target_agent_id: 'a-cfo',
                  target_name: 'Casey',
                  target_role: 'CFO',
                  reason: 'budget sign-off needed',
                },
              ],
            }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MetaGroup />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Dana/ }))
    await user.type(screen.getByLabelText('Message'), 'bring in finance')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText(/budget sign-off needed/)).toBeInTheDocument()
    })
    expect(screen.getByText(/asked to bring in/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Review in Approvals/ })
    expect(link).toHaveAttribute('href', '/approvals')
  })

  it('approves a pending invite in context', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/approvals/:id/approve', ({ params }) =>
        HttpResponse.json(
          apiSuccess({
            id: String(params.id),
            status: 'approved',
            decided_at: '2026-05-19T10:00:00Z',
            decided_by: 'user-1',
          }),
        ),
      ),
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(
          apiSuccess(
            _roundResult({
              pending_invites: [
                {
                  approval_id: 'appr-inv-1',
                  requested_by_agent_id: 'a-ceo',
                  requested_by_name: 'Dana',
                  target_agent_id: 'a-cfo',
                  target_name: 'Casey',
                  target_role: 'CFO',
                  reason: 'budget sign-off needed',
                },
              ],
            }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <MetaGroup />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Dana/ }))
    await user.type(screen.getByLabelText('Message'), 'bring in finance')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText(/budget sign-off needed/)).toBeInTheDocument()
    })

    // In-context consent: approving resolves the invite without leaving
    // the conversation (the default approvals handler grants it).
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => {
      expect(
        screen.getByText(/Casey joins on the next turn/),
      ).toBeInTheDocument()
    })
  })

  it('surfaces a truncation notice when a round stops early', async () => {
    _useRoster()
    server.use(
      http.post('/api/v1/meta/chat/group', () =>
        HttpResponse.json(
          apiSuccess(
            _roundResult({ truncated_reason: 'token_budget_exhausted' }),
          ),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<MetaGroup />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Dana/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: /Dana/ }))
    await user.type(screen.getByLabelText('Message'), 'go')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText(/token budget was exhausted/)).toBeInTheDocument()
    })
  })
})
