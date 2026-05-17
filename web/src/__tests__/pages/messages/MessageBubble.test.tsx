import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageBubble } from '@/pages/messages/MessageBubble'
import { makeMessage } from '../../helpers/factories'

vi.mock('@/hooks/useFlash', () => ({
  useFlash: vi.fn().mockReturnValue({
    flashing: false,
    flashClassName: '',
    triggerFlash: vi.fn(),
    flashStyle: {},
  }),
}))

describe('MessageBubble', () => {
  it('renders sender name and content', () => {
    const msg = makeMessage('1', { sender: 'alice', text: 'Hello world' })
    render(<MessageBubble message={msg} />)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('renders message type badge', () => {
    const msg = makeMessage('1', { type: 'delegation' })
    render(<MessageBubble message={msg} />)
    expect(screen.getByText('Delegation')).toBeInTheDocument()
  })

  it('renders sender avatar', () => {
    const msg = makeMessage('1', { sender: 'alice chen' })
    render(<MessageBubble message={msg} />)
    expect(screen.getByRole('img', { name: 'alice chen' })).toBeInTheDocument()
  })

  it('renders priority indicator for high priority', () => {
    const msg = makeMessage('1', { priority: 'high' })
    render(<MessageBubble message={msg} />)
    expect(screen.getByLabelText('high priority')).toBeInTheDocument()
  })

  it('renders priority indicator for urgent priority', () => {
    const msg = makeMessage('1', { priority: 'urgent' })
    render(<MessageBubble message={msg} />)
    expect(screen.getByLabelText('urgent priority')).toBeInTheDocument()
  })

  it('does not render priority indicator for normal priority', () => {
    const msg = makeMessage('1', { priority: 'normal' })
    render(<MessageBubble message={msg} />)
    expect(screen.queryByLabelText(/priority/)).not.toBeInTheDocument()
  })

  it('renders attachments when present', () => {
    const msg = makeMessage('1', {
      parts: [
        { type: 'text', text: 'see attachment' },
        { type: 'data', data: { ref: 'pr-42' } },
      ],
    })
    render(<MessageBubble message={msg} />)
    expect(screen.getByText('pr-42')).toBeInTheDocument()
  })

  it('calls onClick when clicked', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    const msg = makeMessage('1')
    render(<MessageBubble message={msg} onClick={onClick} />)

    await user.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
