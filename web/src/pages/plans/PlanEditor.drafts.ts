import { useCallback, useState } from 'react'

import type { EditPlanRequest, Plan, PlanItem } from '@/api/types/plans'
import { COMPLEXITY_VALUES, STAKES_VALUES } from '@/api/types/enums'

import type { DraftItem } from './PlanEditor.types'

export function isComplexity(
  value: string,
): value is PlanItem['estimated_complexity'] {
  return (COMPLEXITY_VALUES as readonly string[]).includes(value)
}

export function isStakes(value: string): value is PlanItem['stakes'] {
  return (STAKES_VALUES as readonly string[]).includes(value)
}

export function nonBlankCriteria(
  criteria: readonly string[],
): readonly string[] {
  return criteria.map((c) => c.trim()).filter((c) => c !== '')
}

function toDraft(item: PlanItem): DraftItem {
  return {
    id: item.id,
    title: item.title,
    description: item.description,
    parentId: item.parent_id ?? '',
    owner: item.owner ?? '',
    dependencies: item.dependencies,
    acceptanceCriteria: item.acceptance_criteria,
    expectedArtifacts: item.expected_artifacts,
    requiredSkills: item.required_skills,
    requiredTags: item.required_tags,
    complexity: item.estimated_complexity,
    stakes: item.stakes,
    kind: item.kind,
    options: item.options,
    chosenOptionId: item.chosen_option_id,
    satisfies: item.satisfies,
  }
}

export function toPayload(draft: DraftItem): EditPlanRequest['items'][number] {
  const owner = draft.owner.trim()
  return {
    id: draft.id,
    title: draft.title,
    description: draft.description,
    parent_id: draft.parentId === '' ? null : draft.parentId,
    owner: owner === '' ? null : owner,
    dependencies: draft.dependencies,
    acceptance_criteria: nonBlankCriteria(draft.acceptanceCriteria),
    // Both come from a textarea split on newlines, so a trailing one leaves a
    // blank entry the backend refuses as a 422 after the round trip.
    expected_artifacts: nonBlankCriteria(draft.expectedArtifacts),
    required_skills: draft.requiredSkills,
    required_tags: draft.requiredTags,
    estimated_complexity: draft.complexity,
    stakes: draft.stakes,
    kind: draft.kind,
    options: draft.options,
    chosen_option_id: draft.chosenOptionId,
    satisfies: draft.satisfies,
  }
}

export function acceptanceText(draft: DraftItem): string {
  return draft.acceptanceCriteria.join('\n')
}

export function artifactsText(draft: DraftItem): string {
  return draft.expectedArtifacts.join('\n')
}

/** Whether a draft carries everything the backend requires of its kind. */
export function isComplete(draft: DraftItem): boolean {
  const nonBlank = (lines: readonly string[]) =>
    lines.some((line) => line.trim().length > 0)
  if (!draft.title.trim() || !nonBlank(draft.acceptanceCriteria)) return false
  return draft.kind !== 'work' || nonBlank(draft.expectedArtifacts)
}

export interface DraftList {
  readonly drafts: readonly DraftItem[]
  readonly change: (index: number, patch: Partial<DraftItem>) => void
  readonly remove: (index: number) => void
  readonly add: () => void
}

/**
 * The editable item list and the three edits that keep it consistent.
 *
 * Held apart from the form that renders it because these are the whole state
 * machine: what a removal does to the rows that referenced the removed item
 * is the only non-obvious rule in the editor, and it reads as one piece here
 * rather than buried among field bindings.
 */
export function useDraftItems(plan: Plan): DraftList {
  const [drafts, setDrafts] = useState<readonly DraftItem[]>(() =>
    plan.items.map(toDraft),
  )

  const change = useCallback((index: number, patch: Partial<DraftItem>) => {
    setDrafts((prev) => prev.map((d, i) => (i === index ? { ...d, ...patch } : d)))
  }, [])

  const remove = useCallback((index: number) => {
    setDrafts((prev) => {
      const removed = prev[index]
      if (removed === undefined) return prev
      // Both references to it go, not just the containment one. Whatever hung
      // off it moves to where it sat, and whatever waited on it stops waiting,
      // rather than being left naming an item the plan no longer holds. The
      // backend refuses either, and there is no dependency field here, so an
      // orphaned edge is a 422 the operator has no way to clear.
      return prev
        .filter((_, i) => i !== index)
        .map((draft) => ({
          ...draft,
          parentId:
            draft.parentId === removed.id ? removed.parentId : draft.parentId,
          dependencies: draft.dependencies.filter((id) => id !== removed.id),
        }))
    })
  }, [])

  const add = useCallback(() => {
    setDrafts((prev) => [...prev, newDraft()])
  }, [])

  return { drafts, change, remove, add }
}

function newDraft(): DraftItem {
  return {
    id: crypto.randomUUID(),
    title: '',
    description: '',
    parentId: '',
    owner: '',
    dependencies: [],
    acceptanceCriteria: [],
    expectedArtifacts: [],
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
