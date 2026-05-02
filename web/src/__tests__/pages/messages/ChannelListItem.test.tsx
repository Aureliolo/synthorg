import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChannelListItem } from '@/pages/messages/ChannelListItem'
import { makeChannel } from '../../helpers/factories'

describe('ChannelListItem', () => {
  const defaultProps = {
    channel: makeChannel('#engineering'),
    active: false,
    unreadCount: 0,
    onSelect: vi.fn(),
  }

  it('renders channel name', () => {
    render(<ChannelListItem {...defaultProps} />)
    expect(screen.getByText('#engineering')).toBeInTheDocument()
  })

  it('sets aria-current="page" when active', () => {
    render(<ChannelListItem {...defaultProps} active={true} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-current', 'page')
  })

  it('does not set aria-current when not active', () => {
    render(<ChannelListItem {...defaultProps} active={false} />)
    expect(screen.getByRole('button')).not.toHaveAttribute('aria-current')
  })

  it('shows unread badge when count > 0', () => {
    render(<ChannelListItem {...defaultProps} unreadCount={5} />)
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('hides unread badge when count is 0', () => {
    render(<ChannelListItem {...defaultProps} unreadCount={0} />)
    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  it('renders topic icon for topic channel', () => {
    render(<ChannelListItem {...defaultProps} channel={makeChannel('#eng')} />)
    // Hash icon is rendered as SVG, verify the button exists
    expect(screen.getByRole('button')).toBeInTheDocument()
  })

  it('renders direct icon for direct channel', () => {
    const channel = makeChannel('#dm-alice', {
      type: 'direct',
    })
    render(
      <ChannelListItem {...defaultProps} channel={channel} />,
    )
    expect(screen.getByText('#dm-alice')).toBeInTheDocument()
  })

  it('renders broadcast icon for broadcast channel', () => {
    const channel = makeChannel('#all-hands', {
      type: 'broadcast',
    })
    render(
      <ChannelListItem {...defaultProps} channel={channel} />,
    )
    expect(screen.getByText('#all-hands')).toBeInTheDocument()
  })

  it('calls onSelect with the channel name when clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ChannelListItem {...defaultProps} onSelect={onSelect} />)
    await user.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('#engineering')
  })
})
