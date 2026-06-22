import { render, screen } from '@testing-library/react'
import { PostureBadge } from '@/pages/setup/PostureBadge'

describe('PostureBadge', () => {
  it('renders the posture label', () => {
    render(<PostureBadge posture="autonomous" />)
    expect(screen.getByText('Autonomous')).toBeInTheDocument()
  })

  it('renders nothing when no posture is declared', () => {
    const { container } = render(<PostureBadge posture={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('is keyboard-focusable but not announced as an actionable button', () => {
    render(<PostureBadge posture="autonomous" />)
    const badge = screen.getByText('Autonomous')
    // No role="button": the badge only reveals descriptive text, it has no
    // action, so an interactive role would mislead assistive technology.
    expect(badge).not.toHaveAttribute('role', 'button')
    // Still reachable by keyboard so the explanation tooltip can be opened.
    expect(badge).toHaveAttribute('tabindex', '0')
  })
})
