import { render, screen } from '@testing-library/react'
import type { AbTestArm, AbTestRecord } from '@/api/endpoints/meta'
import { MetaABTestView } from '@/pages/meta/MetaABTestView'

function makeArm(name: string, overrides: Partial<AbTestArm> = {}): AbTestArm {
  return {
    name,
    agent_count: 10,
    fraction: 0.5,
    ...overrides,
  }
}

function makeTest(overrides: Partial<AbTestRecord> = {}): AbTestRecord {
  return {
    id: '550e8400-e29b-41d4-a716-446655440000',
    name: 'Increase collaboration threshold',
    status: 'running',
    verdict: null,
    observation_hours_elapsed: 24,
    arms: [makeArm('control'), makeArm('treatment')],
    created_at: '2026-05-19T09:00:00Z',
    updated_at: '2026-05-19T10:00:00Z',
    ...overrides,
  }
}

describe('MetaABTestView', () => {
  it('renders empty state when no tests', () => {
    render(<MetaABTestView tests={[]} />)
    expect(screen.getByText('No active A/B tests')).toBeInTheDocument()
  })

  it('renders test name for active test', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(
      screen.getByText('Increase collaboration threshold'),
    ).toBeInTheDocument()
  })

  it('shows control and treatment arm agent counts', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(screen.getByText(/control \(10 agents\)/)).toBeInTheDocument()
    expect(screen.getByText(/treatment \(10 agents\)/)).toBeInTheDocument()
  })

  it('shows the status badge', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('shows verdict badge when verdict is set', () => {
    render(<MetaABTestView tests={[makeTest({ verdict: 'treatment_wins' })]} />)
    expect(screen.getByText('Treatment Wins')).toBeInTheDocument()
  })

  it('shows observation hours elapsed', () => {
    render(<MetaABTestView tests={[makeTest()]} />)
    expect(screen.getByText(/24\.0h observation/)).toBeInTheDocument()
  })
})
