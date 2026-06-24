import { render } from '@testing-library/react'
import { MiniOrgChart } from '@/pages/setup/MiniOrgChart'
import type { SetupAgentSummary } from '@/api/types/setup'

function agent(overrides: Partial<SetupAgentSummary>): SetupAgentSummary {
  return {
    name: 'Alice Smith',
    role: 'Developer',
    department: 'engineering',
    level: 'mid',
    model_provider: null,
    model_id: null,
    tier: 'medium',
    personality_preset: null,
    ...overrides,
  }
}

/** Agent rounds expose `${name} - ${role}` via the HTML `title` attribute. */
function agentTitles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[title]')).map(
    (el) => el.getAttribute('title') ?? '',
  )
}

function deptLabels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('span')).map(
    (el) => el.textContent,
  )
}

describe('MiniOrgChart', () => {
  it('renders nothing when there are no agents', () => {
    const { container } = render(<MiniOrgChart agents={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('formats snake_case department names as Title Case labels', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Alice A', department: 'quality_assurance' }),
          agent({ name: 'Bob B', department: 'creative_marketing' }),
        ]}
      />,
    )
    const labels = deptLabels(container)
    expect(labels).toContain('Quality Assurance')
    expect(labels).toContain('Creative Marketing')
  })

  it('labels an empty department as "Unassigned"', () => {
    const { container } = render(
      <MiniOrgChart agents={[agent({ name: 'Orphan One', department: '' })]} />,
    )
    expect(deptLabels(container)).toContain('Unassigned')
  })

  it('renders every agent with an accessible title of name and role', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Alpha', role: 'Engineer', department: 'engineering' }),
          agent({ name: 'Beta', role: 'Designer', department: 'design' }),
          agent({ name: 'Gamma', role: 'Engineer', department: 'engineering' }),
        ]}
      />,
    )
    const titles = agentTitles(container)
    expect(titles).toContain('Alpha - Engineer')
    expect(titles).toContain('Beta - Designer')
    expect(titles).toContain('Gamma - Engineer')
  })

  it('shows a per-department headcount next to each label', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'A', department: 'engineering' }),
          agent({ name: 'B', department: 'engineering' }),
          agent({ name: 'C', department: 'design' }),
        ]}
      />,
    )
    const labels = deptLabels(container)
    // Both department labels and their counts are present.
    expect(labels).toContain('Engineering')
    expect(labels).toContain('Design')
    expect(labels).toContain('2')
    expect(labels).toContain('1')
  })

  it('puts the department holding the most senior agent on top, others below', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Exec One', role: 'CEO', department: 'executive', level: 'c_suite' }),
          agent({ name: 'Dev One', role: 'Engineer', department: 'engineering', level: 'mid' }),
          agent({ name: 'Designer One', role: 'Designer', department: 'design', level: 'mid' }),
        ]}
      />,
    )
    // Leadership box renders before the subordinate row in document order.
    const labels = deptLabels(container).filter((t) =>
      ['Executive', 'Engineering', 'Design'].includes(t),
    )
    expect(labels[0]).toBe('Executive')
    // Connector lines are drawn (border-coloured rules) once a hierarchy exists.
    expect(container.querySelectorAll('.bg-border').length).toBeGreaterThan(0)
  })

  it('falls back to a flat row (no connectors) when no department clearly leads', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Dev One', department: 'engineering', level: 'mid' }),
          agent({ name: 'Designer One', department: 'design', level: 'mid' }),
        ]}
      />,
    )
    // Equal-rank departments: no leadership box, so no connector rules.
    expect(container.querySelectorAll('.bg-border')).toHaveLength(0)
    expect(deptLabels(container)).toContain('Engineering')
    expect(deptLabels(container)).toContain('Design')
  })
})
