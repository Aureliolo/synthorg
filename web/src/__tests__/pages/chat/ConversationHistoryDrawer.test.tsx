import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConversationSummary,
  ConversationTurnRecord,
  getConversationTurns,
  listConversations,
} from '@/api/endpoints/meta'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import { ConversationHistoryDrawer } from '@/pages/chat/ConversationHistoryDrawer'
import { useConversationsStore } from '@/stores/conversations'
import { server } from '@/test-setup'

function conversation(overrides: Partial<ConversationSummary>): ConversationSummary {
  return {
    id: 'c1',
    created_by: 'me',
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-02T00:00:00Z',
    status: 'active',
    kind: 'direct',
    ...overrides,
  }
}

function turn(): ConversationTurnRecord {
  return {
    id: 't1',
    conversation_id: 'c1',
    sequence: 0,
    role: 'user',
    content: 'hello',
    author_agent_id: null,
    author_name: null,
    routed_topic: null,
    routing_confidence: null,
    created_at: '2026-05-01T00:00:00Z',
  }
}

function useList(summaries: ConversationSummary[]): void {
  server.use(
    http.get('/api/v1/meta/chat/conversations', () =>
      HttpResponse.json(paginatedEnvelopeFor<typeof listConversations>(summaries)),
    ),
    http.get('/api/v1/meta/chat/conversations/:id', () =>
      HttpResponse.json(paginatedEnvelopeFor<typeof getConversationTurns>([turn()])),
    ),
  )
}

beforeEach(() => {
  useConversationsStore.getState().resetAll()
})

describe('ConversationHistoryDrawer', () => {
  it('resuming a closed conversation carries its closed state', async () => {
    // Regression: the resume path once hardcoded closed:false, so a closed
    // conversation resumed with an enabled input whose next send 500s.
    useList([conversation({ status: 'closed' })])
    const onResume = vi.fn()
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ConversationHistoryDrawer open onClose={onClose} onResume={onResume} />,
    )

    const button = await screen.findByRole('button', { name: /request work/i })
    await user.click(button)

    await waitFor(() => {
      expect(useConversationsStore.getState().work.closed).toBe(true)
    })
    expect(onResume).toHaveBeenCalledWith('work')
    expect(onClose).toHaveBeenCalled()
  })

  it('resuming an active conversation leaves the input open', async () => {
    useList([conversation({ status: 'active' })])
    const user = userEvent.setup()
    render(
      <ConversationHistoryDrawer open onClose={vi.fn()} onResume={vi.fn()} />,
    )

    await user.click(await screen.findByRole('button', { name: /request work/i }))

    await waitFor(() => {
      expect(useConversationsStore.getState().work.conversationId).toBe('c1')
    })
    expect(useConversationsStore.getState().work.closed).toBe(false)
  })

  it('surfaces a load failure instead of an empty list', async () => {
    server.use(
      http.get('/api/v1/meta/chat/conversations', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 503 }),
      ),
    )
    render(
      <ConversationHistoryDrawer open onClose={vi.fn()} onResume={vi.fn()} />,
    )

    expect(
      await screen.findByText(/could not load conversation history/i),
    ).toBeInTheDocument()
  })
})
