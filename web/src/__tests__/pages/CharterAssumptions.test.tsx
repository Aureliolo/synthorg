import { render, screen } from '@testing-library/react'
import { CharterDraftCard } from '@/pages/chat/CharterDraftCard'
import type { ProjectCharter } from '@/api/types/charter'

/**
 * The operator can tell their own answers from the org's proposals.
 *
 * A charter authorises a body of work and a budget, and the interview fills
 * whatever the human did not settle. Rendered identically, an invented
 * success criterion reads exactly like an agreed one, and the initiative's
 * whole tail is later scored against those criteria: approving without
 * noticing decides the project. So a facet the interview supplied itself is
 * marked where it is rendered and named once above the lists.
 */
function makeCharter(overrides: Partial<ProjectCharter> = {}): ProjectCharter {
  return {
    id: 'charter-1',
    title: 'Browser Falling-Blocks Puzzle Game',
    brief: 'A single-player falling-blocks game playable in a browser',
    goals: ['Deliver the core loop'],
    constraints: ['Single player only'],
    success_criteria: ['Reachable at a public URL'],
    scope: { in_scope: ['Core game loop'], out_of_scope: ['Multiplayer'] },
    assumed_facets: [],
    envelope: { amount: 500, currency: 'EUR', deadline: null },
    status: 'drafted',
    task_id: null,
    project_id: null,
    forecast_id: null,
    conversation_id: 'conv-1',
    correlation_id: null,
    created_at: '2026-08-19T00:00:00Z',
    updated_at: '2026-08-19T00:00:00Z',
    created_by: 'ceo',
    approved_at: null,
    approved_by: null,
    proposed_project_name: null,
    proposed_project_description: '',
    version: 1,
    ...overrides,
  } as ProjectCharter
}

function renderCharter(charter: ProjectCharter) {
  render(
    <CharterDraftCard
      charter={charter}
      busy={false}
      onSave={vi.fn()}
      onApprove={vi.fn()}
      onCancel={vi.fn()}
    />,
  )
}

describe('a charter carrying the org’s own assumptions', () => {
  it('says so before the lists, in the operator’s terms', () => {
    renderCharter(makeCharter({ assumed_facets: ['success_criteria'] }))
    expect(screen.getByText(/not your answer/i)).toBeInTheDocument()
    expect(screen.getByText(/what counts as done/i)).toBeInTheDocument()
  })

  it('marks the section the assumption landed in', () => {
    renderCharter(makeCharter({ assumed_facets: ['success_criteria'] }))
    // One badge, on the one assumed facet: a marker on every section would
    // carry no information at all.
    expect(screen.getAllByText('Assumed')).toHaveLength(1)
  })

  it('marks both scope lists, which are one facet', () => {
    renderCharter(makeCharter({ assumed_facets: ['scope'] }))
    expect(screen.getAllByText('Assumed')).toHaveLength(2)
  })

  it('marks the budget field, which the banner also names', () => {
    // The envelope is the one assumed facet with no list of its own, so a
    // marker that only reaches the lists names it in the banner and leaves
    // the field it is talking about unmarked.
    renderCharter(makeCharter({ assumed_facets: ['envelope'] }))
    expect(screen.getAllByText('Assumed')).toHaveLength(1)
    expect(screen.getByLabelText('Budget')).toBeInTheDocument()
  })

  it('says nothing when the human settled everything', () => {
    renderCharter(makeCharter())
    expect(screen.queryByText('Assumed')).toBeNull()
    expect(screen.queryByText(/not your answer/i)).toBeNull()
  })
})
