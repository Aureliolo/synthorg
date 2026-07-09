import { render, screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { AgentCard } from '@/components/ui/agent-card'

describe('AgentCard', () => {
  const defaultProps = {
    name: 'Alice Smith',
    role: 'Software Engineer',
    department: 'Engineering',
    status: 'active' as const,
  }

  it('renders agent name', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.getByText('Alice Smith')).toBeInTheDocument()
  })

  it('renders agent role', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.getByText('Software Engineer')).toBeInTheDocument()
  })

  it('renders department', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.getByText(/Engineering/)).toBeInTheDocument()
  })

  it('renders avatar with initials', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.getByText('AS')).toBeInTheDocument()
  })

  it('renders status badge', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.getByLabelText('Active')).toBeInTheDocument()
  })

  it('renders current task when provided', () => {
    render(<AgentCard {...defaultProps} currentTask="Fix authentication bug" />)

    expect(screen.getByText(/Fix authentication bug/)).toBeInTheDocument()
  })

  it('does not render task section when no task', () => {
    render(<AgentCard {...defaultProps} />)

    expect(screen.queryByText(/Task:/)).not.toBeInTheDocument()
  })

  it('renders the model with its capability tier as a suffix', () => {
    render(<AgentCard {...defaultProps} model="example-large-001" tier="large" />)

    expect(screen.getByText('example-large-001')).toBeInTheDocument()
    expect(screen.getByText('· large')).toBeInTheDocument()
    expect(screen.queryByText('Tier:')).not.toBeInTheDocument()
  })

  it('renders a standalone tier row when a tier has no model', () => {
    render(<AgentCard {...defaultProps} tier="medium" />)

    expect(screen.getByText('Tier:')).toBeInTheDocument()
    expect(screen.getByText('medium')).toBeInTheDocument()
  })

  it('renders the model with no tier suffix when the tier is absent', () => {
    render(<AgentCard {...defaultProps} model="example-large-001" />)

    expect(screen.getByText('example-large-001')).toBeInTheDocument()
    expect(screen.queryByText(/·/)).not.toBeInTheDocument()
    expect(screen.queryByText('Tier:')).not.toBeInTheDocument()
  })

  it('renders timestamp when provided', () => {
    render(<AgentCard {...defaultProps} timestamp="2m ago" />)

    expect(screen.getByText('2m ago')).toBeInTheDocument()
  })

  it('renders error status correctly', () => {
    render(<AgentCard {...defaultProps} status="error" />)

    expect(screen.getByLabelText('Error')).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(<AgentCard {...defaultProps} className="my-class" />)

    expect(container.firstChild).toHaveClass('my-class')
  })

  it('renders initials for any valid two-word name (property)', () => {
    fc.assert(
      fc.property(
        fc.tuple(
          fc.string({ minLength: 1, maxLength: 20 }),
          fc.string({ minLength: 1, maxLength: 20 }),
        )
          .filter(([first, last]) => /^[A-Za-z]/.test(first) && /^[A-Za-z]/.test(last))
          .map(([first, last]) => `${first} ${last}`),
        (name) => {
          const { unmount } = render(
            <AgentCard {...defaultProps} name={name} />,
          )
          const words = name.trim().split(/\s+/)
          const expectedInitials = words.length >= 2
            ? `${words[0]![0]!.toUpperCase()}${words[words.length - 1]![0]!.toUpperCase()}`
            : words[0]![0]!.toUpperCase()
          expect(screen.getByText(expectedInitials)).toBeInTheDocument()
          unmount()
        },
      ),
    )
  })
})
