import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageList } from '@/pages/messages/MessageList'
import { makeMessage } from '../../helpers/factories'

vi.mock('@/hooks/useFlash', () => ({
  useFlash: vi.fn().mockReturnValue({
    flashing: false,
    flashClassName: '',
    triggerFlash: vi.fn(),
    flashStyle: {},
  }),
}))

describe('MessageList', () => {
  const defaultProps = {
    messages: [] as ReturnType<typeof makeMessage>[],
    expandedThreads: new Set<string>(),
    toggleThread: vi.fn(),
    onSelectMessage: vi.fn(),
    hasMore: false,
    loadingMore: false,
    onLoadMore: vi.fn(),
  }

  it('renders empty state when no messages', () => {
    render(<MessageList {...defaultProps} />)
    expect(screen.getByText('No messages')).toBeInTheDocument()
  })

  it('renders messages with date dividers', () => {
    const msgs = [
      makeMessage('1', {
        timestamp: '2026-03-28T10:00:00Z',
        text: 'First message',
      }),
      makeMessage('2', {
        timestamp: '2026-03-28T14:00:00Z',
        text: 'Second message',
      }),
    ]
    render(<MessageList {...defaultProps} messages={msgs} />)
    expect(screen.getByText('First message')).toBeInTheDocument()
    expect(screen.getByText('Second message')).toBeInTheDocument()
    // Verify date divider(s) rendered
    expect(
      screen.getAllByRole('separator').length,
    ).toBeGreaterThanOrEqual(1)
  })

  it('renders Load earlier messages button when hasMore', () => {
    const msgs = [makeMessage('1', { text: 'Test' })]
    render(<MessageList {...defaultProps} messages={msgs} hasMore={true} />)
    expect(screen.getByRole('button', { name: /load earlier messages/i })).toBeInTheDocument()
  })

  it('calls onLoadMore when Load button clicked', async () => {
    const user = userEvent.setup()
    const onLoadMore = vi.fn()
    const msgs = [makeMessage('1', { text: 'Test' })]
    render(<MessageList {...defaultProps} messages={msgs} hasMore={true} onLoadMore={onLoadMore} />)

    await user.click(screen.getByRole('button', { name: /load earlier messages/i }))
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })

  it('shows loading state when loadingMore', () => {
    const msgs = [makeMessage('1', { text: 'Test' })]
    render(<MessageList {...defaultProps} messages={msgs} hasMore={true} loadingMore={true} />)
    expect(screen.getByRole('button', { name: /loading/i })).toBeDisabled()
  })

  it('groups threaded messages together', () => {
    const msgs = [
      makeMessage('1', {
        text: 'Thread start',
        timestamp: '2026-03-28T10:00:00Z',
        metadata: { task_id: 'task-1', project_id: null, tokens_used: null, cost: null, extra: [] },
      }),
      makeMessage('2', {
        text: 'Thread reply',
        timestamp: '2026-03-28T10:05:00Z',
        metadata: { task_id: 'task-1', project_id: null, tokens_used: null, cost: null, extra: [] },
      }),
    ]
    render(<MessageList {...defaultProps} messages={msgs} />)
    // Thread should show "1 more in thread" pill
    expect(screen.getByText(/1 more in thread/)).toBeInTheDocument()
  })

  it('has aria-live attribute for accessibility', () => {
    const msgs = [makeMessage('1', { text: 'Test' })]
    render(<MessageList {...defaultProps} messages={msgs} />)
    expect(screen.getByLabelText('Messages')).toHaveAttribute('aria-live', 'polite')
  })
})
