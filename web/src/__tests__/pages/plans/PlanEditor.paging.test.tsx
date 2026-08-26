import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { ROWS_PER_PAGE, usePlanEditorRows } from '@/pages/plans/PlanEditor.paging'
import type { DraftItem } from '@/pages/plans/PlanEditor.types'

// What `api/dto_plans.py` accepts, which is the size the editor has to stay
// usable at. Mounting a plan this big through the DOM costs seconds, so the
// bound is asserted here, where the decision is actually made.
const MAX_ITEMS = 1000

function makeWrapper(initialEntries: readonly string[] = ['/']) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter initialEntries={[...initialEntries]}>{children}</MemoryRouter>
  }
}

function draft(index: number): DraftItem {
  return {
    id: `i${String(index)}`,
    title: `Task ${String(index)}`,
    description: '',
    parentId: '',
    owner: '',
    dependencies: [],
    acceptanceCriteria: ['it works'],
    expectedArtifacts: ['src/a.ts'],
    requiredSkills: [],
    requiredTags: [],
    complexity: 'medium',
    stakes: 'normal',
    kind: 'work',
    options: [],
    chosenOptionId: null,
    satisfies: [],
  }
}

function drafts(count: number): readonly DraftItem[] {
  return Array.from({ length: count }, (_, index) => draft(index + 1))
}

function rowsFor(items: readonly DraftItem[], entries?: readonly string[]) {
  return renderHook(() => usePlanEditorRows(items, vi.fn()), {
    wrapper: makeWrapper(entries),
  })
}

describe('usePlanEditorRows', () => {
  it('holds one page of rows whatever the plan costs to hold whole', () => {
    // The regression this guards is quadratic, not merely large: before the
    // window, one option list was built per draft rather than per shown row,
    // so a plan at the cap produced a million option objects per keystroke.
    const { result } = rowsFor(drafts(MAX_ITEMS))

    expect(result.current.shown).toHaveLength(ROWS_PER_PAGE)
    expect(result.current.choices).toHaveLength(ROWS_PER_PAGE)
  })

  it('offers every item in the plan as a container, not only the page', () => {
    // Containment is a property of the whole plan: an item two pages away is
    // still a legal parent, so windowing the rows must not narrow the choices.
    const { result } = rowsFor(drafts(ROWS_PER_PAGE * 3))
    const [first] = result.current.choices

    // Every draft except the subject itself, plus the no-parent option.
    expect(first).toHaveLength(ROWS_PER_PAGE * 3)
  })

  it('reports where the page starts so a row can number itself in the plan', () => {
    const { result } = rowsFor(drafts(ROWS_PER_PAGE * 3), ['/?planItemsPage=3'])

    expect(result.current.firstShown).toBe(ROWS_PER_PAGE * 2)
    expect(result.current.shown[0]?.title).toBe(`Task ${String(ROWS_PER_PAGE * 2 + 1)}`)
  })

  it('offers no pager to a plan that fits on one page', () => {
    // A control with nowhere to go reads as broken, and most plans are small.
    const { result } = rowsFor(drafts(ROWS_PER_PAGE))

    expect(result.current.pager).toBeUndefined()
  })

  it('offers a pager as soon as the plan outgrows one page', () => {
    const { result } = rowsFor(drafts(ROWS_PER_PAGE + 1))

    expect(result.current.pager?.total).toBe(ROWS_PER_PAGE + 1)
  })
})
