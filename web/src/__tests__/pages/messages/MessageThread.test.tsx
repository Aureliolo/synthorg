import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageThread } from '@/pages/messages/MessageThread'
import { makeMessage } from '../../helpers/factories'

vi.mock('@/hooks/useFlash', () => ({
  useFlash: vi.fn().mockReturnValue({
    flashing: false,
    flashClassName: '',
    triggerFlash: vi.fn(),
    flashStyle: {},
  }),
}))

describe('MessageThread', () => {
  const defaultProps = {
    expanded: false,
    onToggle: vi.fn(),
    onSelectMessage: vi.fn(),
  }

  it('renders single message without thread UI', () => {
    const msgs = [makeMessage('1', { text: 'Solo message' })]
    render(<MessageThread {...defaultProps} messages={msgs} />)
    expect(screen.getByText('Solo message')).toBeInTheDocument()
    expect(screen.queryByText(/more in thread/)).not.toBeInTheDocument()
  })

  it('renders first message and thread pill when collapsed', () => {
    const msgs = [
      makeMessage('1', { text: 'First' }),
      makeMessage('2', { text: 'Second' }),
      makeMessage('3', { text: 'Third' }),
    ]
    render(<MessageThread {...defaultProps} messages={msgs} />)
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('2 more in thread')).toBeInTheDocument()
  })

  it('calls onToggle when thread pill is clicked', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    const msgs = [makeMessage('1'), makeMessage('2')]
    render(<MessageThread {...defaultProps} messages={msgs} onToggle={onToggle} />)

    await user.click(screen.getByText('1 more in thread'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('shows all messages when expanded', () => {
    const msgs = [
      makeMessage('1', { text: 'First' }),
      makeMessage('2', { text: 'Second' }),
      makeMessage('3', { text: 'Third' }),
    ]
    render(<MessageThread {...defaultProps} messages={msgs} expanded={true} />)
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
    expect(screen.getByText('Third')).toBeInTheDocument()
  })

  it('shows Collapse thread text when expanded', () => {
    const msgs = [makeMessage('1'), makeMessage('2')]
    render(<MessageThread {...defaultProps} messages={msgs} expanded={true} />)
    expect(screen.getByText('Collapse thread')).toBeInTheDocument()
  })

  it('calls onSelectMessage when message is clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const msgs = [makeMessage('msg-1', { text: 'Click me' })]
    render(<MessageThread {...defaultProps} messages={msgs} onSelectMessage={onSelect} />)

    await user.click(screen.getByText('Click me'))
    expect(onSelect).toHaveBeenCalledWith('msg-1')
  })

  it('returns null for empty messages array', () => {
    const { container } = render(<MessageThread {...defaultProps} messages={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
