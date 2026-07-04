import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type {
  ChiefOfStaffFlags,
  getConversationTurns,
  listConversations,
  MetaConfig,
} from '@/api/endpoints/meta'
import { apiSuccess } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import ChatPage from '@/pages/ChatPage'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

const BASE_FLAGS: ChiefOfStaffFlags = {
  chat_enabled: true,
  propose_enabled: true,
  group_chat_enabled: true,
  direct_mcp_enabled: false,
  chat_model: 'test-provider/example-medium-001',
  propose_model: 'test-provider/example-large-001',
  routing_model: 'test-provider/example-small-001',
  narrative_model: 'test-provider/example-medium-001',
  direct_mcp_ready: false,
}

function configWith(overrides: Partial<ChiefOfStaffFlags>): MetaConfig {
  return {
    enabled: true,
    chief_of_staff_enabled: true,
    chief_of_staff: { ...BASE_FLAGS, ...overrides },
    config_tuning_enabled: false,
    architecture_proposals_enabled: false,
    prompt_tuning_enabled: false,
    code_modification_enabled: false,
  }
}

function renderMode(mode: string, overrides: Partial<ChiefOfStaffFlags>) {
  useMetaStore.setState({ config: configWith(overrides) })
  return render(
    <MemoryRouter initialEntries={[`/?mode=${mode}`]}>
      <ChatPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  useMetaStore.setState({ config: null })
})

describe('ChatPage mode gating', () => {
  it('surfaces the missing-model notice when an enabled mode has no model', () => {
    renderMode('staff', { chat_enabled: true, chat_model: null })
    expect(screen.getByText('No model is configured for this mode')).toBeInTheDocument()
    expect(screen.getByText(/chief_of_staff\.chat_model/)).toBeInTheDocument()
  })

  it('does not gate a mode whose model is configured', () => {
    renderMode('staff', { chat_enabled: true })
    expect(
      screen.queryByText('No model is configured for this mode'),
    ).not.toBeInTheDocument()
  })

  it('warns that direct action is inert without security governance', () => {
    renderMode('action', { direct_mcp_enabled: true, direct_mcp_ready: false })
    expect(screen.getByText('This mode is enabled but not yet live')).toBeInTheDocument()
    expect(screen.getByText(/security\.mcp_self_consumer/)).toBeInTheDocument()
  })

  it('does not warn once direct action is live', () => {
    renderMode('action', { direct_mcp_enabled: true, direct_mcp_ready: true })
    expect(
      screen.queryByText('This mode is enabled but not yet live'),
    ).not.toBeInTheDocument()
  })

  it('shows the switched-off notice when the flag is disabled', () => {
    renderMode('action', { direct_mcp_enabled: false })
    expect(
      screen.getByText('This conversation mode is switched off'),
    ).toBeInTheDocument()
  })
})

describe('ChatPage transcript persistence', () => {
  beforeEach(() => {
    useMetaStore.setState({
      config: configWith({}),
      chatLoading: false,
      proposals: [],
      alerts: [],
      error: null,
    })
  })

  it('keeps a mode transcript when switching modes and back', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(
          apiSuccess({
            answer: 'Signals look healthy.',
            sources: [],
            confidence: 0.8,
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    )

    // Send a message in the default Chief of Staff mode.
    await user.type(screen.getByLabelText('Chat message'), 'how are signals?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => {
      expect(screen.getByText('Signals look healthy.')).toBeInTheDocument()
    })

    // Switch to Request work (unmounts the Chief of Staff panel) and back.
    await user.click(screen.getByRole('radio', { name: 'Request work' }))
    expect(screen.queryByText('Signals look healthy.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Chief of Staff' }))

    // The transcript survived the remount: it lives in the store, not in
    // the unmounted panel's local state.
    expect(screen.getByText('Signals look healthy.')).toBeInTheDocument()
    expect(screen.getByText('how are signals?')).toBeInTheDocument()
  })
})

describe('ChatPage conversation resume', () => {
  it('resumes a past conversation into its mode from the History drawer', async () => {
    server.use(
      http.get('/api/v1/meta/chat/conversations', () =>
        HttpResponse.json(
          paginatedEnvelopeFor<typeof listConversations>([
            {
              id: 'conv-1',
              created_by: 'ceo',
              created_at: '2026-06-30T10:00:00Z',
              updated_at: '2026-06-30T10:05:00Z',
              status: 'active',
              kind: 'direct',
            },
          ]),
        ),
      ),
      http.get('/api/v1/meta/chat/conversations/conv-1', () =>
        HttpResponse.json(
          paginatedEnvelopeFor<typeof getConversationTurns>([
            {
              id: 'turn-1',
              conversation_id: 'conv-1',
              sequence: 0,
              role: 'user',
              content: 'What is our runway?',
              author_agent_id: null,
              author_name: null,
              routed_topic: null,
              routing_confidence: null,
              created_at: '2026-06-30T10:00:00Z',
            },
            {
              id: 'turn-2',
              conversation_id: 'conv-1',
              sequence: 1,
              role: 'assistant',
              content: 'About fourteen months at the current burn.',
              author_agent_id: null,
              author_name: null,
              routed_topic: null,
              routing_confidence: null,
              created_at: '2026-06-30T10:05:00Z',
            },
          ]),
        ),
      ),
    )
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: /history/i }))
    const resumeButton = await screen.findByRole('button', {
      name: /request work/i,
    })
    await user.click(resumeButton)

    // The drawer hydrated the Request-work slice and switched modes, so the
    // persisted turns render in the resumed transcript.
    await waitFor(() => {
      expect(screen.getByText('What is our runway?')).toBeInTheDocument()
    })
    expect(
      screen.getByText('About fourteen months at the current burn.'),
    ).toBeInTheDocument()
  })
})
