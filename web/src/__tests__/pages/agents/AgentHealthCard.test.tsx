import { render, screen } from '@testing-library/react'
import type { AgentHealthResponse } from '@/api/types'
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
})
