import { render, screen } from '@testing-library/react'
import type { AgentHealthResponse, AgentUnavailability } from '@/api/types/agents'
import { AgentHealthCard } from '@/pages/agents/AgentHealthCard'

function makeHealth(
  overrides: Partial<AgentHealthResponse> = {},
): AgentHealthResponse {
  return {
    agent_id: '11111111-2222-3333-4444-555555555555',
    agent_name: 'Alice',
    last_active_at: '2026-04-19T08:30:00Z',
    lifecycle_status: 'active',
    performance: null,
    unavailable: null,
    is_available: true,
    ...overrides,
  }
}

function makeUnavailable(
  overrides: Partial<AgentUnavailability> = {},
): AgentUnavailability {
  return {
    provider_name: 'test-provider',
    model: 'test-capable-001',
    verdict: 'down',
    outcome_class: null,
    since: '2026-04-19T08:00:00Z',
    needs_operator: false,
    reason: 'test-provider/test-capable-001 is failing most recent calls',
    ...overrides,
  }
}

describe('AgentHealthCard', () => {
  it('renders lifecycle and last-active when health is present', () => {
    render(<AgentHealthCard health={makeHealth()} />)
    expect(screen.getByText('Health')).toBeInTheDocument()
    expect(screen.getByText('Lifecycle')).toBeInTheDocument()
    expect(screen.getByText('Last active')).toBeInTheDocument()
  })

  it('falls back to a placeholder when last-active is absent', () => {
    render(<AgentHealthCard health={makeHealth({ last_active_at: null })} />)
    expect(screen.getByText('Last active').nextElementSibling).toHaveTextContent('--')
  })

  it('renders nothing when health is null', () => {
    const { container } = render(<AgentHealthCard health={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('says the agent is taking work when its model serves', () => {
    render(<AgentHealthCard health={makeHealth()} />)
    expect(screen.getByText('Taking work')).toBeInTheDocument()
  })

  it('shows why an agent is out rather than only that it is', () => {
    render(
      <AgentHealthCard health={makeHealth({ unavailable: makeUnavailable() })} />,
    )
    expect(screen.getByText('Model down')).toBeInTheDocument()
    expect(
      screen.getByText(/test-provider\/test-capable-001 is failing/),
    ).toBeInTheDocument()
  })

  it('separates a failure that clears itself from one that will not', () => {
    render(
      <AgentHealthCard
        health={makeHealth({
          unavailable: makeUnavailable({
            needs_operator: true,
            outcome_class: 'payment_required',
            reason:
              'test-provider/test-capable-001 is returning payment_required; ' +
              'this does not clear without an operator',
          }),
        })}
      />,
    )
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText(/does not clear without an operator/)).toBeInTheDocument()
  })

  it('keeps an ACTIVE lifecycle from reading as available on its own', () => {
    render(
      <AgentHealthCard
        health={makeHealth({
          lifecycle_status: 'active',
          unavailable: makeUnavailable(),
        })}
      />,
    )
    expect(screen.queryByText('Taking work')).not.toBeInTheDocument()
  })
})
