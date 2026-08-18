import { render, screen } from '@testing-library/react'
import { CharterDraftCard } from '@/pages/chat/CharterDraftCard'
import { awaitsDispatch } from '@/stores/charter'
import type { ProjectCharter } from '@/api/types/charter'

/**
 * A charter the operator authorised and that never ran is still reachable.
 *
 * The invariant is about where the charter LIVES, not about one failure mode:
 * the draft only ever arrived on a turn result, so it existed in one browser
 * tab and nowhere else. A reload, a second tab, or a dispatch that failed
 * after the approval was recorded all left an operator with a charter the
 * backend holds, treats as resumable (`require_dispatchable` admits APPROVED
 * with no run), and the dashboard could not show or act on. The panel is
 * where that gap closes, so the panel is where it is pinned.
 */
function makeCharter(overrides: Partial<ProjectCharter> = {}): ProjectCharter {
  return {
    id: 'charter-1',
    title: 'Browser Falling-Blocks Puzzle Game v1',
    brief: 'A single-player falling-blocks game playable in a browser',
    goals: ['Deliver the core loop'],
    constraints: ['Single player only'],
    success_criteria: ['An automated test suite passes'],
    scope: { in_scope: ['Core game loop'], out_of_scope: ['Multiplayer'] },
    assumed_facets: [],
    envelope: { amount: 500, currency: 'EUR', deadline: null },
    status: 'drafted',
    task_id: null,
    project_id: null,
    forecast_id: null,
    conversation_id: 'conv-1',
    correlation_id: null,
    created_at: '2026-08-18T21:45:00Z',
    updated_at: '2026-08-18T21:45:00Z',
    created_by: 'cto',
    approved_at: null,
    approved_by: null,
    proposed_project_name: null,
    proposed_project_description: '',
    version: 1,
    ...overrides,
  } as ProjectCharter
}

describe('a charter still awaiting its run', () => {
  it('counts a draft', () => {
    expect(awaitsDispatch(makeCharter())).toBe(true)
  })

  it('counts an approval whose run never started', () => {
    expect(
      awaitsDispatch(makeCharter({ status: 'approved', task_id: null })),
    ).toBe(true)
  })

  it('does not count an approval that named a run', () => {
    expect(
      awaitsDispatch(makeCharter({ status: 'approved', task_id: 'task-1' })),
    ).toBe(false)
  })

  it('does not count a cancelled charter', () => {
    expect(awaitsDispatch(makeCharter({ status: 'cancelled' }))).toBe(false)
  })

  it('offers a way to start the run that never started', () => {
    render(
      <CharterDraftCard
        charter={makeCharter({ status: 'approved', task_id: null })}
        busy={false}
        onSave={vi.fn()}
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /start the run/i })).toBeInTheDocument()
    expect(screen.getByText(/never started/i)).toBeInTheDocument()
    // The decision is already recorded, so the brief is no longer editable:
    // what is outstanding is the run, not the operator's answer.
    expect(screen.queryByRole('button', { name: /save changes/i })).toBeNull()
  })

  it('leaves a dispatched charter with nothing to press', () => {
    render(
      <CharterDraftCard
        charter={makeCharter({ status: 'approved', task_id: 'task-1' })}
        busy={false}
        onSave={vi.fn()}
        onApprove={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /start the run/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
  })
})
