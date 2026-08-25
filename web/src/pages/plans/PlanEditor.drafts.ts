import type { EditPlanRequest, PlanItem } from '@/api/types/plans'
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

export function toDraft(item: PlanItem): DraftItem {
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
    expected_artifacts: draft.expectedArtifacts,
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

export function newDraft(): DraftItem {
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
