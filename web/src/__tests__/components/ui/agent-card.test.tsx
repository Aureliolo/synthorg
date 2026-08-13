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

  describe('model capabilities', () => {
    it('lists the assigned model capabilities', () => {
      render(<AgentCard {...defaultProps} capabilities={['reasoning', 'vision']} />)

      expect(screen.getByText('reasoning, vision')).toBeInTheDocument()
    })

    it('says unverified when capabilities were never measured', () => {
      render(<AgentCard {...defaultProps} capabilitiesUnverified />)

      expect(screen.getByText('unverified')).toBeInTheDocument()
    })

    it('prefers real capabilities over the unverified caveat', () => {
      render(
        <AgentCard {...defaultProps} capabilities={['reasoning']} capabilitiesUnverified />,
      )

      expect(screen.getByText('reasoning')).toBeInTheDocument()
      expect(screen.queryByText('unverified')).not.toBeInTheDocument()
    })

    it('reports an unresolved binding distinctly from a plain model', () => {
      // The whole point of the state: a missing model must not look like a
      // healthy model that happens to have no extra capabilities.
      const { unmount } = render(<AgentCard {...defaultProps} modelBindingUnresolved />)
      expect(screen.getByText('model not found')).toBeInTheDocument()
      unmount()

      render(<AgentCard {...defaultProps} />)
      expect(screen.queryByText('model not found')).not.toBeInTheDocument()
      expect(screen.queryByText(/Capabilities/)).not.toBeInTheDocument()
    })

    it('outranks every other capability wording when the model is missing', () => {
      render(
        <AgentCard
          {...defaultProps}
          modelBindingUnresolved
          capabilities={['reasoning']}
          capabilitiesUnverified
        />,
      )

      expect(screen.getByText('model not found')).toBeInTheDocument()
      expect(screen.queryByText('reasoning')).not.toBeInTheDocument()
    })

    it('names a provider-config outage rather than blaming the binding', () => {
      // The binding may be perfectly healthy; the org just cannot read its
      // provider config right now. Reporting "model not found" here would
      // accuse every agent at once.
      render(<AgentCard {...defaultProps} capabilitiesUnavailable />)

      expect(screen.getByText('provider config unavailable')).toBeInTheDocument()
      expect(screen.queryByText('model not found')).not.toBeInTheDocument()
    })

    it('outranks every other capability wording during an outage', () => {
      render(
        <AgentCard
          {...defaultProps}
          capabilitiesUnavailable
          modelBindingUnresolved
          capabilities={['reasoning']}
          capabilitiesUnverified
        />,
      )

      expect(screen.getByText('provider config unavailable')).toBeInTheDocument()
      expect(screen.queryByText('model not found')).not.toBeInTheDocument()
      expect(screen.queryByText('reasoning')).not.toBeInTheDocument()
    })

    it('warns only when runtime proved tool calling fails', () => {
      const { unmount } = render(<AgentCard {...defaultProps} toolCallsFailed />)
      expect(screen.getByText('No tool calling')).toBeInTheDocument()
      unmount()

      render(<AgentCard {...defaultProps} toolCallsFailed={false} />)
      expect(screen.queryByText('No tool calling')).not.toBeInTheDocument()
    })
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

  it('renders the model with its capability rung as a suffix', () => {
    render(
      <AgentCard {...defaultProps} model="example-expert-001" capability="expert" />,
    )

    expect(screen.getByText('example-expert-001')).toBeInTheDocument()
    expect(screen.getByText('expert')).toBeInTheDocument()
    // The separator dot is decorative and hidden from assistive tech.
    expect(screen.getByText('·')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.queryByText('Capability:')).not.toBeInTheDocument()
  })

  it('renders a standalone capability row when a rung has no model', () => {
    render(<AgentCard {...defaultProps} capability="capable" />)

    expect(screen.getByText('Capability:')).toBeInTheDocument()
    expect(screen.getByText('capable')).toBeInTheDocument()
  })

  it('renders the model with no suffix when the rung is absent', () => {
    render(<AgentCard {...defaultProps} model="example-expert-001" />)

    expect(screen.getByText('example-expert-001')).toBeInTheDocument()
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
