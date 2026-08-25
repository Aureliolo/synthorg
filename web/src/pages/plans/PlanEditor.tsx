import { useCallback, useMemo, useState } from 'react'

import { Plus, Trash2 } from 'lucide-react'

import type { EditPlanRequest, Plan, PlanItem } from '@/api/types/plans'
import { COMPLEXITY_VALUES, STAKES_VALUES } from '@/api/types/enums'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import type { SelectOption } from '@/components/ui/select-field'
import { SelectField } from '@/components/ui/select-field'
import { usePlansStore } from '@/stores/plans'
import { isUnroutableOwner } from '@/utils/plans'

const COMPLEXITY_OPTIONS = COMPLEXITY_VALUES.map((v) => ({ value: v, label: v }))
const STAKES_OPTIONS = STAKES_VALUES.map((v) => ({ value: v, label: v }))

// Mirror the backend field bounds (api/dto_plans.py) so an over-long or
// over-count edit is caught in the browser rather than after a 422 round trip.
const TITLE_MAX = 256
const TEXT_MAX = 8192
const MAX_ITEMS = 1000
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

function toPayload(draft: DraftItem): EditPlanRequest['items'][number] {
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

interface RowProps {
  index: number
  draft: DraftItem
  canRemove: boolean
  roster: ReadonlySet<string> | undefined
  /** What this row may be moved under, computed against the whole draft set. */
  parentChoices: readonly SelectOption[]
  onChange: (index: number, patch: Partial<DraftItem>) => void
  onRemove: (index: number) => void
}

interface GradingProps {
  index: number
  draft: DraftItem
  onChange: (index: number, patch: Partial<DraftItem>) => void
}

/** The message for an owner the org cannot route to, or null when it can. */
function ownerError(
  owner: string,
  roster: ReadonlySet<string> | undefined,
): string | null {
  const trimmed = owner.trim()
  if (trimmed === '' || !isUnroutableOwner(trimmed, roster)) return null
  return `No agent holds the role "${trimmed}". Pick a role the org staffs, or leave the item unassigned.`
}

const UNASSIGNED_OWNER: SelectOption = { value: '', label: 'Unassigned' }

const NO_PARENT: SelectOption = { value: '', label: 'No parent (a workstream)' }

/**
 * Every draft's own id mapped to the ids of its children.
 *
 * Built once per draft list rather than re-derived per row: the closure below
 * is a walk over the whole list, and running one inside each of a thousand
 * rows on every keystroke is the shape that makes a plain edit unusable.
 */
function childIndex(
  drafts: readonly DraftItem[],
): ReadonlyMap<string, readonly string[]> {
  const children = new Map<string, string[]>()
  for (const draft of drafts) {
    if (draft.parentId === '') continue
    const kids = children.get(draft.parentId)
    if (kids === undefined) children.set(draft.parentId, [draft.id])
    else kids.push(draft.id)
  }
  return children
}

/**
 * The items this one may be moved under.
 *
 * Everything the backend would refuse is left out rather than offered and
 * rejected after a round trip: itself, anything already below it (which would
 * close a containment cycle), and a decision, which is chosen rather than
 * decomposed so nothing can hang off one. The backend still enforces all
 * three; this only keeps the operator from being told no.
 */
function parentOptions(
  drafts: readonly DraftItem[],
  children: ReadonlyMap<string, readonly string[]>,
  index: number,
): readonly SelectOption[] {
  const subject = drafts[index]
  if (subject === undefined) return [NO_PARENT]
  // Walked down from the subject rather than swept repeatedly over the list,
  // so list order cannot hide a grandchild whose parent comes later, and the
  // cost is the subtree rather than the plan. The frontier is bounded by the
  // draft count because each id is added once, which is also what stops a
  // cycle an unsaved edit can hold from spinning here.
  const below = new Set<string>([subject.id])
  const frontier = [subject.id]
  while (frontier.length > 0) {
    for (const child of children.get(frontier.pop() as string) ?? []) {
      if (below.has(child)) continue
      below.add(child)
      frontier.push(child)
    }
  }
  return [
    NO_PARENT,
    ...drafts
      .filter((draft) => !below.has(draft.id) && draft.kind !== 'decision')
      .map((draft) => ({
        value: draft.id,
        label: draft.title.trim() === '' ? 'Untitled item' : draft.title,
      })),
  ]
}

/**
 * What this item belongs to.
 *
 * Containment says what an item is part of and never when it runs, which
 * dependencies alone decide. Moving an item under another makes that other one
 * an assembly of it: it stops being dispatched as work and starts assembling
 * what sits below it instead.
 */
function ParentField({
  index,
  draft,
  options,
  onChange,
}: GradingProps & { options: readonly SelectOption[] }) {
  return (
    <SelectField
      label="Belongs to"
      options={options}
      value={draft.parentId}
      hint="An item with children is assembled from them rather than done directly."
      onChange={(value) => onChange(index, { parentId: value })}
    />
  )
}

/**
 * The owning role for an item.
 *
 * Offered as a choice whenever the roster is known, because the backend
 * accepts only a role the org staffs and a free-text field invites the
 * invented near-miss ("Backend Engineer" for a "Backend Developer" org) that
 * left a plan's items undispatchable. An owner already outside the roster
 * still shows as itself, flagged, so the operator can see what to replace.
 * With no roster to offer, the field stays free text rather than presenting
 * an empty list of choices.
 */
function OwnerField({ index, draft, roster, onChange }: GradingProps & {
  roster: ReadonlySet<string> | undefined
}) {
  if (roster === undefined || roster.size === 0) {
    return (
      <InputField
        label="Owner (role)"
        value={draft.owner}
        maxLength={TITLE_MAX}
        onValueChange={(value) => onChange(index, { owner: value })}
      />
    )
  }
  const options = [
    UNASSIGNED_OWNER,
    ...[...roster].sort().map((role) => ({ value: role, label: role })),
  ]
  return (
    <SelectField
      label="Owner (role)"
      options={options}
      value={draft.owner}
      error={ownerError(draft.owner, roster)}
      hint="Only a role the org staffs can be dispatched to."
      onChange={(value) => onChange(index, { owner: value })}
    />
  )
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

function PlanEditorRow({
  index,
  draft,
  canRemove,
  roster,
  parentChoices,
  onChange,
  onRemove,
}: RowProps) {
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
      <OwnerField index={index} draft={draft} roster={roster} onChange={onChange} />
      <ParentField
        index={index}
        draft={draft}
        options={parentChoices}
        onChange={onChange}
      />
      <ItemGradingFields index={index} draft={draft} onChange={onChange} />
    </div>
  )
}

export interface PlanEditorProps {
  plan: Plan
  /** The roles the org staffs, or `undefined` while unknown. */
  roster: ReadonlySet<string> | undefined
  onDone: () => void
}

/** Editable form for reworking a plan's items, producing a new revision. */
export function PlanEditor({ plan, roster, onDone }: PlanEditorProps) {
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
  // criterion (capped at MAX_CRITERIA), a dispatchable owner or none, and, for
  // a work item, at least one expected deliverable. Gate the save on all of
  // them rather than surfacing the 422 after a round trip.
  const children = useMemo(() => childIndex(drafts), [drafts])

  const canSave =
    drafts.length > 0 &&
    drafts.length <= MAX_ITEMS &&
    drafts.every(
      (d) =>
        isComplete(d) &&
        nonBlankCriteria(d.acceptanceCriteria).length <= MAX_CRITERIA &&
        ownerError(d.owner, roster) === null,
    )

  return (
    <div className="space-y-3">
      {drafts.map((draft, index) => (
        <PlanEditorRow
          key={draft.id}
          index={index}
          draft={draft}
          canRemove={drafts.length > 1}
          roster={roster}
          parentChoices={parentOptions(drafts, children, index)}
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
