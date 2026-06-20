import { render, screen } from '@testing-library/react'
import type { EvolutionSummary } from '@/api/endpoints/meta'
import { MetaEvolutionView } from '@/pages/meta/MetaEvolutionView'

function makeSummary(overrides: Partial<EvolutionSummary> = {}): EvolutionSummary {
  return {
    total_proposals: 12,
    approval_rate: 0.75,
    most_adapted_axis: 'identity',
    recent_outcomes: [
      {
        agent_id: 'agent-ceo',
        axis: 'identity',
        applied: true,
        proposed_at: '2026-05-19T09:00:00Z',
      },
    ],
    ...overrides,
  }
}

const axes = [
  { axis: 'identity', count: 7 },
  { axis: 'prompt_template', count: 3 },
]

describe('MetaEvolutionView', () => {
  it('renders empty state with no data', () => {
    render(<MetaEvolutionView summary={null} axes={[]} />)
    expect(screen.getByText('No evolution outcomes yet')).toBeInTheDocument()
  })

  it('renders the approval rate metric', () => {
    render(<MetaEvolutionView summary={makeSummary()} axes={axes} />)
    expect(screen.getByText('75%')).toBeInTheDocument()
  })

  it('renders the most adapted axis', () => {
    render(<MetaEvolutionView summary={makeSummary()} axes={axes} />)
    expect(screen.getAllByText('identity').length).toBeGreaterThan(0)
  })

  it('renders an applied outcome badge', () => {
    render(<MetaEvolutionView summary={makeSummary()} axes={axes} />)
    expect(screen.getByText('Applied')).toBeInTheDocument()
  })

  it('renders axis stats when summary is absent but axes present', () => {
    render(<MetaEvolutionView summary={null} axes={axes} />)
    expect(screen.getByText('Outcomes by axis')).toBeInTheDocument()
  })
})
