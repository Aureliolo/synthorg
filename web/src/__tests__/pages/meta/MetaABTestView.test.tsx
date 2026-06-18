import { render, screen } from '@testing-library/react'
import type { ABTestSummary, ABTestGroupMetrics } from '@/api/endpoints/meta'
import { MetaABTestView } from '@/pages/meta/MetaABTestView'

function makeGroupMetrics(
  group: 'control' | 'treatment',
  overrides: Partial<ABTestGroupMetrics> = {},
): ABTestGroupMetrics {
  return {
    group,
    agent_count: 10,
    observation_count: 20,
    avg_quality_score: 7.5,
    avg_success_rate: 0.85,
    total_spend: 100.0,
    ...overrides,
  }
}

function makeTest(overrides: Partial<ABTestSummary> = {}): ABTestSummary {
  return {
    proposal_id: '550e8400-e29b-41d4-a716-446655440000',
    proposal_title: 'Increase collaboration threshold',
    control_metrics: makeGroupMetrics('control'),
    treatment_metrics: makeGroupMetrics('treatment'),
    verdict: null,
    observation_hours_elapsed: 24,
    observation_hours_total: 48,
    ...overrides,
  }
}

describe('MetaABTestView', () => {
  it('renders empty state when no tests', () => {
    render(<MetaABTestView tests={[]} />)
    expect(screen.getByText('No active A/B tests')).toBeInTheDocument()
  })

  it('renders proposal title for active test', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(
      screen.getByText('Increase collaboration threshold'),
    ).toBeInTheDocument()
  })

  it('shows control and treatment agent counts', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(screen.getByText(/Control \(10 agents\)/)).toBeInTheDocument()
    expect(screen.getByText(/Treatment \(10 agents\)/)).toBeInTheDocument()
  })

  it('shows verdict badge when verdict is set', () => {
    render(
      <MetaABTestView tests={[makeTest({ verdict: 'treatment_wins' })]} />,
    )
    expect(screen.getByText('Treatment Wins')).toBeInTheDocument()
  })

  it('shows observation progress', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(screen.getByText(/24\.0h \/ 48h/)).toBeInTheDocument()
  })
})
