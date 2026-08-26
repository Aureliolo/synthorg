import type { PlanItem } from '@/api/types/plans'
import type { SelectOption } from '@/components/ui/select-field'

/** One plan item as the editor holds it while the operator is still typing. */
export interface DraftItem {
  id: string
  title: string
  description: string
  /** The item this one was split out of; `''` makes it a workstream. */
  parentId: string
  owner: string
  dependencies: readonly string[]
  acceptanceCriteria: readonly string[]
  expectedArtifacts: readonly string[]
  requiredSkills: readonly string[]
  requiredTags: readonly string[]
  complexity: PlanItem['estimated_complexity']
  stakes: PlanItem['stakes']
  // Preserved verbatim so editing a plan that holds a decision item does not
  // strip its options and fail the decision validator on save, and so a rework
  // keeps each item's objective-criteria coverage.
  kind: PlanItem['kind']
  options: PlanItem['options']
  chosenOptionId: PlanItem['chosen_option_id']
  satisfies: PlanItem['satisfies']
}

/** What every field of one row reads and writes back. */
export interface GradingProps {
  index: number
  draft: DraftItem
  onChange: (index: number, patch: Partial<DraftItem>) => void
}

/** What one whole row of the editor is given. */
export interface RowProps {
  index: number
  draft: DraftItem
  canRemove: boolean
  roster: ReadonlySet<string> | undefined
  /** What this row may be moved under, computed against the whole draft set. */
  parentChoices: readonly SelectOption[]
  /** The plan's objective criteria, which is the whole claimable vocabulary. */
  objectiveCriteria: readonly string[]
  onChange: (index: number, patch: Partial<DraftItem>) => void
  onRemove: (index: number) => void
}
