import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChannelSidebar } from '@/pages/messages/ChannelSidebar'
import { makeChannel } from '../../helpers/factories'

describe('ChannelSidebar', () => {
  const defaultProps = {
    channels: [
      makeChannel('#engineering'),
      makeChannel('#product'),
      makeChannel('#dm-alice', { type: 'direct' as const }),
    ],
    activeChannel: null as string | null,
    unreadCounts: {} as Record<string, number>,
    onSelectChannel: vi.fn(),
    loading: false,
  }

  // Below the lg breakpoint the same channel list renders inside a
  // mobile Drawer trigger button (which surfaces the active channel
  // name as its label). jsdom ignores Tailwind's responsive
  // visibility, so both branches end up in the DOM. Scoping every
  // query to the desktop ``nav`` landmark keeps the assertions
  // unambiguous regardless of which branch the runner happens to
  // measure first.
  function desktop() {
    return within(screen.getByRole('navigation', { name: 'Channels' }))
  }

  it('renders channel names', () => {
    render(<ChannelSidebar {...defaultProps} />)
    expect(desktop().getByText('#engineering')).toBeInTheDocument()
    expect(desktop().getByText('#product')).toBeInTheDocument()
    expect(desktop().getByText('#dm-alice')).toBeInTheDocument()
  })

  it('groups channels by type', () => {
    render(<ChannelSidebar {...defaultProps} />)
    expect(desktop().getByText('Topics')).toBeInTheDocument()
    expect(desktop().getByText('Direct')).toBeInTheDocument()
  })

  it('highlights active channel', () => {
    render(<ChannelSidebar {...defaultProps} activeChannel="#engineering" />)
    const active = desktop().getByText('#engineering').closest('button')
    expect(active).toHaveAttribute('aria-current', 'page')
  })

  it('shows unread badge count', () => {
    render(<ChannelSidebar {...defaultProps} unreadCounts={{ '#product': 5 }} />)
    expect(desktop().getByText('5')).toBeInTheDocument()
  })

  it('hides unread badge when count is zero', () => {
    render(<ChannelSidebar {...defaultProps} unreadCounts={{ '#product': 0 }} />)
    // Should not have a badge element for 0
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument()
  })

  it('calls onSelectChannel when clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ChannelSidebar {...defaultProps} onSelectChannel={onSelect} />)

    await user.click(desktop().getByText('#product'))
    expect(onSelect).toHaveBeenCalledWith('#product')
  })

  it('shows skeleton when loading with no channels', () => {
    render(<ChannelSidebar {...defaultProps} channels={[]} loading={true} />)
    expect(screen.getByLabelText('Channels')).toBeInTheDocument()
  })

  it('shows empty state when no channels', () => {
    render(<ChannelSidebar {...defaultProps} channels={[]} />)
    expect(screen.getAllByText('No channels').length).toBeGreaterThan(0)
  })
})
