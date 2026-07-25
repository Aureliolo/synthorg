import { useCallback, useState } from 'react'

import { Plus, Trash2 } from 'lucide-react'

import type { EditPlanRequest, Plan, PlanItem } from '@/api/types/plans'
import { COMPLEXITY_VALUES, STAKES_VALUES } from '@/api/types/enum-values.gen'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { usePlansStore } from '@/stores/plans'

const COMPLEXITY_OPTIONS = COMPLEXITY_VALUES.map((v) => ({ value: v, label: v }))
const STAKES_OPTIONS = STAKES_VALUES.map((v) => ({ value: v, label: v }))

// Mirror the backend field bounds (api/dto_plans.py) so an over-long or
// over-count edit is caught in the browser rather than after a 422 round trip.
const TITLE_MAX = 256
const TEXT_MAX = 8192
const MAX_ITEMS = 50
const MAX_CRITERIA = 50

function isComplexity(value: string): value is PlanItem['estimated_complexity'] {
  return (COMPLEXITY_VALUES as readonly string[]).includes(value)
}

function isStakes(value: string): value is PlanItem['stakes'] {
  return (STAKES_VALUES as readonly string[]).includes(value)
}

function nonBlankCriteria(criteria: readonly string[]): readonly string[] {
  return criteria.map((c) => c.trim()).filter((c) => c !== '')
}

interface DraftItem {
  id: string
  title: string
  description: string
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

function toDraft(item: PlanItem): DraftItem {
  return {
    id: item.id,
    title: item.title,
    description: item.description,
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

function toPayload(draft: DraftItem): EditPlanRequest['items'][number] {
  const owner = draft.owner.trim()
  return {
    id: draft.id,
    title: draft.title,
    description: draft.description,
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

function acceptanceText(draft: DraftItem): string {
  return draft.acceptanceCriteria.join('\n')
}

function artifactsText(draft: DraftItem): string {
  return draft.expectedArtifacts.join('\n')
}

/** Whether a draft carries everything the backend requires of its kind. */
function isComplete(draft: DraftItem): boolean {
  const nonBlank = (lines: readonly string[]) =>
    lines.some((line) => line.trim().length > 0)
  if (!draft.title.trim() || !nonBlank(draft.acceptanceCriteria)) return false
  return draft.kind !== 'work' || nonBlank(draft.expectedArtifacts)
}

function newDraft(): DraftItem {
  return {
    id: crypto.randomUUID(),
    title: '',
    description: '',
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

interface RowProps {
  index: number
  draft: DraftItem
  canRemove: boolean
  onChange: (index: number, patch: Partial<DraftItem>) => void
  onRemove: (index: number) => void
}

interface GradingProps {
  index: number
  draft: DraftItem
  onChange: (index: number, patch: Partial<DraftItem>) => void
}

function ItemGradingFields({ index, draft, onChange }: GradingProps) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap">
      <SelectField
        label="Complexity"
        options={COMPLEXITY_OPTIONS}
        value={draft.complexity}
        onChange={(value) => {
          if (isComplexity(value)) onChange(index, { complexity: value })
        }}
      />
      <SelectField
        label="Stakes"
        options={STAKES_OPTIONS}
        value={draft.stakes}
        onChange={(value) => {
          if (isStakes(value)) onChange(index, { stakes: value })
        }}
      />
    </div>
  )
}

function PlanEditorRow({ index, draft, canRemove, onChange, onRemove }: RowProps) {
  return (
    <div className="space-y-3 rounded-md border border-border p-card">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">Item {index + 1}</span>
        {canRemove && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove item ${String(index + 1)}`}
            onClick={() => onRemove(index)}
          >
            <Trash2 />
          </Button>
        )}
      </div>
      <InputField
        label="Title"
        value={draft.title}
        maxLength={TITLE_MAX}
        onValueChange={(value) => onChange(index, { title: value })}
      />
      <InputField
        label="Description"
        multiline
        rows={2}
        value={draft.description}
        maxLength={TEXT_MAX}
        onValueChange={(value) => onChange(index, { description: value })}
      />
      <InputField
        label="Acceptance criteria (one per line)"
        multiline
        rows={2}
        required
        value={acceptanceText(draft)}
        maxLength={TEXT_MAX}
        hint="Every item needs at least one criterion that defines done."
        onValueChange={(value) =>
          onChange(index, { acceptanceCriteria: value.split('\n') })
        }
      />
      <InputField
        label="Expected deliverables (one per line)"
        multiline
        rows={2}
        required={draft.kind === 'work'}
        value={artifactsText(draft)}
        maxLength={TEXT_MAX}
        hint={
          draft.kind === 'work'
            ? 'A work item that declares none is rejected: the deliverables arm the zero-artifact guard.'
            : 'A decision item builds nothing, so leave this empty.'
        }
        onValueChange={(value) =>
          onChange(index, { expectedArtifacts: value.split('\n') })
        }
      />
      <InputField
        label="Owner (role or agent)"
        value={draft.owner}
        maxLength={TITLE_MAX}
        onValueChange={(value) => onChange(index, { owner: value })}
      />
      <ItemGradingFields index={index} draft={draft} onChange={onChange} />
    </div>
  )
}

export interface PlanEditorProps {
  plan: Plan
  onDone: () => void
}

/** Editable form for reworking a plan's items, producing a new revision. */
export function PlanEditor({ plan, onDone }: PlanEditorProps) {
  const [drafts, setDrafts] = useState<readonly DraftItem[]>(() =>
    plan.items.map(toDraft),
  )
  const [saving, setSaving] = useState(false)

  const handleChange = useCallback((index: number, patch: Partial<DraftItem>) => {
    setDrafts((prev) =>
      prev.map((d, i) => (i === index ? { ...d, ...patch } : d)),
    )
  }, [])

  const handleRemove = useCallback((index: number) => {
    setDrafts((prev) => prev.filter((_, i) => i !== index))
  }, [])

  const handleAdd = useCallback(() => {
    setDrafts((prev) => [...prev, newDraft()])
  }, [])

  const handleSave = useCallback(async () => {
    setSaving(true)
    const result = await usePlansStore
      .getState()
      .editPlan(plan.id, { items: drafts.map(toPayload) })
    setSaving(false)
    if (result) onDone()
  }, [plan.id, drafts, onDone])

  // The backend requires every item to carry a title, at least one acceptance
  // criterion (capped at MAX_CRITERIA), and, for a work item, at least one
  // expected deliverable. Gate the save on all of them rather than surfacing
  // the 422 after a round trip.
  const canSave =
    drafts.length > 0 &&
    drafts.length <= MAX_ITEMS &&
    drafts.every(
      (d) =>
        isComplete(d) && nonBlankCriteria(d.acceptanceCriteria).length <= MAX_CRITERIA,
    )

  return (
    <div className="space-y-3">
      {drafts.map((draft, index) => (
        <PlanEditorRow
          key={draft.id}
          index={index}
          draft={draft}
          canRemove={drafts.length > 1}
          onChange={handleChange}
          onRemove={handleRemove}
        />
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleAdd}
          disabled={drafts.length >= MAX_ITEMS}
        >
          <Plus aria-hidden="true" />
          Add item
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" size="sm" onClick={onDone} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} disabled={!canSave || saving}>
          {saving ? 'Saving…' : 'Save revision'}
        </Button>
      </div>
    </div>
  )
}
