import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { AgentGridView } from '@/pages/agents/AgentGridView'
import { makeAgent } from '../../helpers/factories'

function renderWithRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('AgentGridView', () => {
  it('renders empty state when no agents', () => {
    renderWithRouter(<AgentGridView agents={[]} />)
    expect(screen.getByText('No agents yet')).toBeInTheDocument()
    // The page renders its own filtered-to-nothing state before this one, so
    // this branch means an empty roster and must not name filters: with none
    // set, "adjust your filters" points at something the operator cannot act
    // on.
    expect(screen.queryByText(/filters/i)).not.toBeInTheDocument()
  })

  it('renders agent cards when agents provided', () => {
    const agents = [makeAgent('alice'), makeAgent('bob')]
    renderWithRouter(<AgentGridView agents={agents} />)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
  })

  it('links to agent detail URL by agent id (not name)', () => {
    // Agent URLs are id-based now -- display names can contain
    // arbitrary characters and URL-encoding them caused backend
    // lookup failures.  The factory assigns `id: 'agent-{name}'`,
    // so the link should point at the id, not the name.
    const agents = [makeAgent('alice doe')]
    renderWithRouter(<AgentGridView agents={agents} />)
    const link = screen.getByRole('link', { name: /alice doe/i })
    expect(link).toHaveAttribute('href', '/agents/agent-alice%20doe')
  })
})
