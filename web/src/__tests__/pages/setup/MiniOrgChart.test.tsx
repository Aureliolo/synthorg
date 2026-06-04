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

/**
 * SVG `<title>` child nodes are the tooltip mechanism used across
 * MiniOrgChart's agent circles and department labels. Testing Library's
 * `getByTitle` doesn't match SVG `<title>` elements by content reliably
 * across jsdom versions, so we query directly from the container.
 */
function getTitles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('title')).map(
    (t) => t.textContent,
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
    const texts = Array.from(container.querySelectorAll('text')).map(
      (el) => el.textContent,
    )
    expect(texts).toContain('Quality Assurance')
    expect(texts).toContain('Creative Marketing')
    // No truncation ellipsis -- covers both the ASCII `...` form
    // and the Unicode ellipsis `\u2026`, so a regression that
    // switches to either style would still fail.
    for (const t of texts) expect(t).not.toMatch(/(?:\.{2,}|\u2026)$/)
  })

  it('marks the highest-seniority agent in each department as the head', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Ada Junior', role: 'Junior Dev', level: 'junior' }),
          agent({ name: 'Bill Lead', role: 'Lead Dev', level: 'lead' }),
          agent({ name: 'Cal Mid', role: 'Dev', level: 'mid' }),
        ]}
      />,
    )
    const titles = getTitles(container)
    const headTitles = titles.filter((t) => t.includes('(head)'))
    expect(headTitles).toHaveLength(1)
    expect(headTitles[0]).toContain('Bill Lead')
    expect(headTitles[0]).toContain('Lead Dev')
    expect(headTitles[0]).toContain('lead')
  })

  it('when all agents have null level, still picks exactly one head (first-encountered tiebreak)', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Unlevelled A', level: null }),
          agent({ name: 'Unlevelled B', level: null }),
        ]}
      />,
    )
    const titles = getTitles(container)
    // Both agent titles present (rendered without level suffix).
    expect(titles.some((t) => t.startsWith('Unlevelled A'))).toBe(true)
    expect(titles.some((t) => t.startsWith('Unlevelled B'))).toBe(true)
    // ``pickHead`` still designates one agent as head even when all
    // levels are null: the first agent encountered in the insertion
    // order becomes the head via the ``levelRank === -1`` tiebreak.
    expect(titles.filter((t) => t.includes('(head)'))).toHaveLength(1)
  })

  it('picks a c_suite executive as head even over a lead in the same dept', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Exec One', role: 'CEO', department: 'executive', level: 'c_suite' }),
          agent({ name: 'Lead One', role: 'Team Lead', department: 'executive', level: 'lead' }),
        ]}
      />,
    )
    const titles = getTitles(container)
    const headTitles = titles.filter((t) => t.includes('(head)'))
    expect(headTitles).toHaveLength(1)
    expect(headTitles[0]).toContain('Exec One')
    expect(headTitles[0]).toContain('CEO')
    expect(headTitles[0]).toContain('c-suite')
  })

  it('groups unknown department under "unassigned" when empty', () => {
    const { container } = render(
      <MiniOrgChart agents={[agent({ name: 'Orphan One', department: '' })]} />,
    )
    const labels = Array.from(container.querySelectorAll('text')).map(
      (el) => el.textContent,
    )
    expect(labels).toContain('Unassigned')
  })

  it('renders every agent as a node with an accessible title', () => {
    const { container } = render(
      <MiniOrgChart
        agents={[
          agent({ name: 'Alpha', department: 'engineering' }),
          agent({ name: 'Beta', department: 'design' }),
          agent({ name: 'Gamma', department: 'engineering' }),
        ]}
      />,
    )
    const titles = getTitles(container)
    expect(titles.some((t) => t.startsWith('Alpha'))).toBe(true)
    expect(titles.some((t) => t.startsWith('Beta'))).toBe(true)
    expect(titles.some((t) => t.startsWith('Gamma'))).toBe(true)
  })
})
