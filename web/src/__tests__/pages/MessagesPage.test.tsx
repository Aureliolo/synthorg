import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import MessagesPage from '@/pages/MessagesPage'
import { makeMessage, makeChannel } from '../helpers/factories'
import type { UseMessagesDataReturn } from '@/hooks/useMessagesData'

const defaultReturn: UseMessagesDataReturn = {
  channels: [],
  channelsLoading: false,
  channelsError: null,
  unreadCounts: {},
  channelsWithMessages: new Set<string>(),
  messages: [],
  total: 0,
  loading: false,
  loadingMore: false,
  error: null,
  hasMore: false,
  expandedThreads: new Set(),
  toggleThread: vi.fn(),
  newMessageIds: new Set(),
  fetchMore: vi.fn(),
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultReturn }
const getMessagesData = vi.fn(() => hookReturn)

// Computed key avoids Vitest hoist transform inlining the identifier
vi.mock('@/hooks/useMessagesData', () => {
  const hookName = 'useMessagesData'
  return { [hookName]: () => getMessagesData() }
})

vi.mock('@/stores/messages', () => ({
  useMessagesStore: {
    getState: vi.fn(() => ({
      clearNewMessageIds: vi.fn(),
    })),
  },
}))

function renderPage(route = '/messages') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MessagesPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  hookReturn = {
    ...defaultReturn,
    expandedThreads: new Set(),
    newMessageIds: new Set(),
  }
  vi.clearAllMocks()
})

describe('MessagesPage', () => {
  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultReturn, loading: true, channelsLoading: true, expandedThreads: new Set() }
    renderPage()
    expect(screen.getByLabelText('Loading messages')).toBeInTheDocument()
  })

  it('renders page heading', () => {
    hookReturn = { ...defaultReturn, channels: [makeChannel('#eng')], expandedThreads: new Set() }
    renderPage()
    expect(screen.getByRole('heading', { name: 'Messages' })).toBeInTheDocument()
  })

  it('renders error banner when error exists', () => {
    hookReturn = { ...defaultReturn, error: 'Fetch failed', channels: [makeChannel('#eng')], expandedThreads: new Set() }
    renderPage()
    expect(screen.getByText('Fetch failed')).toBeInTheDocument()
  })

  it('renders WS disconnected banner when setup error', () => {
    hookReturn = {
      ...defaultReturn,
      wsConnected: false,
      wsSetupError: 'WebSocket failed.',
      channels: [makeChannel('#eng')],
      expandedThreads: new Set(),
    }
    renderPage()
    expect(screen.getByText('WebSocket failed.')).toBeInTheDocument()
  })

  it('renders select channel prompt when no channel selected', () => {
    hookReturn = { ...defaultReturn, channels: [makeChannel('#eng')], expandedThreads: new Set() }
    renderPage('/messages')
    expect(screen.getByText('Select a channel')).toBeInTheDocument()
  })

  it('renders channel sidebar with channels', () => {
    // The sidebar buckets channels into an "Active" group (current
    // selection / unread / known to carry messages) and a collapsed
    // "Empty" group. Mark both channels as carrying messages so they
    // land in the Active group and the rendered DOM exposes their
    // names without the user having to expand the empty section.
    hookReturn = {
      ...defaultReturn,
      channels: [makeChannel('#engineering'), makeChannel('#product')],
      channelsWithMessages: new Set(['#engineering', '#product']),
      expandedThreads: new Set(),
    }
    renderPage()
    expect(screen.getByText('#engineering')).toBeInTheDocument()
    expect(screen.getByText('#product')).toBeInTheDocument()
  })

  it('renders empty state when channel has no messages', () => {
    hookReturn = {
      ...defaultReturn,
      channels: [makeChannel('#eng')],
      messages: [],
      total: 0,
      expandedThreads: new Set(),
    }
    renderPage('/messages?channel=%23eng')
    expect(screen.getByText('No messages')).toBeInTheDocument()
  })

  it('renders messages when channel has data', () => {
    hookReturn = {
      ...defaultReturn,
      channels: [makeChannel('#eng')],
      messages: [
        makeMessage('1', { content: 'Hello world', sender: 'alice' }),
      ],
      total: 1,
      expandedThreads: new Set(),
    }
    renderPage('/messages?channel=%23eng')
    expect(screen.getByText('Hello world')).toBeInTheDocument()
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('does not show WS banner on initial load', () => {
    hookReturn = {
      ...defaultReturn,
      wsConnected: false,
      wsSetupError: null,
      channels: [makeChannel('#eng')],
      expandedThreads: new Set(),
      newMessageIds: new Set(),
    }
    renderPage()
    expect(screen.queryByText(/real-time updates disconnected/i)).not.toBeInTheDocument()
  })

  it('renders filter bar when channel has type filter', () => {
    hookReturn = {
      ...defaultReturn,
      channels: [makeChannel('#eng')],
      messages: [
        makeMessage('1', { type: 'delegation' }),
      ],
      total: 1,
      expandedThreads: new Set(),
      newMessageIds: new Set(),
    }
    renderPage('/messages?channel=%23eng&type=delegation')
    expect(screen.getByText('Messages')).toBeInTheDocument()
  })

  it('ignores invalid type param', () => {
    hookReturn = {
      ...defaultReturn,
      channels: [makeChannel('#eng')],
      messages: [makeMessage('1')],
      total: 1,
      expandedThreads: new Set(),
      newMessageIds: new Set(),
    }
    // 'bogus' is not a valid MessageType
    renderPage('/messages?channel=%23eng&type=bogus')
    // Should still render messages (invalid filter ignored)
    expect(screen.getByText('Message 1 content')).toBeInTheDocument()
  })

  it('shows both errors when both exist', () => {
    hookReturn = {
      ...defaultReturn,
      error: 'Messages error',
      channelsError: 'Channels error',
      channels: [makeChannel('#eng')],
      expandedThreads: new Set(),
      newMessageIds: new Set(),
    }
    renderPage()
    expect(screen.getByText('Messages error')).toBeInTheDocument()
    expect(screen.getByText('Channels error')).toBeInTheDocument()
  })
})
