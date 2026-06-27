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

  // The priority dot is decorative (aria-hidden); priority is conveyed through
  // the button's composite accessible name instead of a nested label.
  it.each(['high', 'urgent'] as const)(
    'exposes %s priority in the message button accessible name',
    (priority) => {
      const msg = makeMessage('1', { priority })
      render(<MessageBubble message={msg} />)
      expect(
        screen.getByRole('button', {
          name: (accessibleName) => accessibleName.includes(`${priority} priority`),
        }),
      ).toBeInTheDocument()
    },
  )

  it('does not mention priority for normal priority', () => {
    const msg = makeMessage('1', { priority: 'normal' })
    render(<MessageBubble message={msg} />)
    expect(
      screen.queryByRole('button', { name: /priority/ }),
    ).not.toBeInTheDocument()
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

  it('calls onSelect with the message id when clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const msg = makeMessage('1')
    render(<MessageBubble message={msg} onSelect={onSelect} />)

    await user.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(msg.id)
  })
})
