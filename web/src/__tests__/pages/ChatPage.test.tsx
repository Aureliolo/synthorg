import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import type {
  getConversationTurns,
  listConversations,
  postTurn,
} from '@/api/endpoints/meta'
import { paginatedEnvelopeFor, successFor } from '@/mocks/handlers/helpers'
import { deferredStreamBody, sseResponse } from '@/mocks/handlers/meta'
import ChatPage from '@/pages/ChatPage'
import { useOrgQuestionsStore } from '@/stores/org-questions'
import { server } from '@/test-setup'

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  )
}

describe('ChatPage unified surface', () => {
  it('shows the empty state with example prompts before any turn', async () => {
    renderChat()
    expect(screen.getByText('Talk to your organisation')).toBeInTheDocument()
    expect(
      screen.getByText('What is the organisation working on right now?'),
    ).toBeInTheDocument()
    // The unified surface has no legacy ask/act/charter mode picker: the intent
    // is classified server-side per turn, so no tab strip is ever rendered.
    expect(screen.queryByRole('tablist')).toBeNull()
    // The page hydrates its parked questions on mount; settle that read so its
    // state update lands inside act() rather than after the test.
    await waitFor(() =>
      expect(useOrgQuestionsStore.getState().loading).toBe(false),
    )
  })

  it('sends a message to the unified turn endpoint and renders the answer', async () => {
    const user = userEvent.setup()
    renderChat()

    await user.type(
      screen.getByLabelText('Message the organisation'),
      'how are we doing?',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    // The operator's message and the org's answer both render in the one
    // transcript, no mode picker involved.
    expect(screen.getByText('how are we doing?')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('The organisation is healthy.')).toBeInTheDocument()
    })
  })

  it('renders parked steering as an inline event card', async () => {
    server.use(
      // A side-effecting intent never streams: the stream classifies and defers
      // back to the buffered endpoint, which returns the parked directives.
      http.post('/api/v1/meta/chat/turn/stream', () =>
        sseResponse(deferredStreamBody('propose')),
      ),
      http.post('/api/v1/meta/chat/turn', () =>
        HttpResponse.json(
          successFor<typeof postTurn>({
            intent: 'propose',
            intent_reason: 'classified',
            intent_confidence: 0.9,
            conversation_id: 'conv-1',
            answer: null,
            propose: {
              conversation_id: 'conv-1',
              status: 'proposed',
              clarifying_question: null,
              conversation_closed: false,
              responder_role: null,
              responder_name: null,
              routed_topic: null,
              routing_confidence: null,
              routing_reason: 'no_role_router',
              steering: [
                {
                  text: 'Use Postgres, not Mongo',
                  approval_id: 'appr-1',
                  kind: 'redirect',
                  project: 'Growth',
                },
              ],
            },
            group: null,
            act: null,
            configure: null,
            charter: null,
            chime_ins: [],
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    renderChat()

    await user.type(
      screen.getByLabelText('Message the organisation'),
      'switch the store to Postgres',
    )
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Queued 1 steering directive for your confirmation.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('Confirm steering')).toBeInTheDocument()
    // By role, not by text: the directive is the card's only control, and a
    // keyboard user reaches its approval through that link or not at all.
    expect(
      screen.getByRole('link', { name: /Use Postgres, not Mongo/ }),
    ).toBeInTheDocument()
  })

  it('resumes a past conversation from the History drawer', async () => {
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
              // What the backend derives from the opening turn below.
              title: 'What is our runway?',
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
    renderChat()

    await user.click(screen.getByRole('button', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /request work/i }))

    await waitFor(() => {
      expect(screen.getByText('What is our runway?')).toBeInTheDocument()
    })
    expect(
      screen.getByText('About fourteen months at the current burn.'),
    ).toBeInTheDocument()
  })
})

describe('what scrolls on the chat page', () => {
  /**
   * A live run scrolled the composer off the bottom of the screen and filed a
   * message into the wrong place. The transcript is meant to be the only thing
   * that scrolls: everything above it in the column has to stay put, which
   * needs every box between the page and the transcript to be bounded by the
   * page rather than sized to its own content.
   */
  it('bounds the empty state instead of letting it size the column', async () => {
    renderChat()

    const empty = screen.getByText('Talk to your organisation').closest('div.flex-1')

    // `min-h-0` is the half that is easy to lose: without it a flex item's
    // automatic minimum is its content, so the scroll box grows the column it
    // was supposed to be bounded by and nothing overflows anywhere.
    expect(empty?.className).toMatch(/\bmin-h-0\b/)
    expect(empty?.className).toMatch(/\boverflow-y-auto\b/)

    await waitFor(() =>
      expect(useOrgQuestionsStore.getState().loading).toBe(false),
    )
  })
})
