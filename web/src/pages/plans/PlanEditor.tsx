import { useCallback, useState } from 'react'

import { Plus, Trash2 } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { COMPLEXITY_VALUES, STAKES_VALUES } from '@/api/types/enums'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { Pagination } from '@/components/ui/pagination'
import type { SelectOption } from '@/components/ui/select-field'
import { SelectField } from '@/components/ui/select-field'
import { usePlansStore } from '@/stores/plans'
import { isUnroutableOwner } from '@/utils/plans'

import { usePlanEditorRows } from './PlanEditor.paging'
import {
  acceptanceText,
  artifactsText,
  isComplete,
  isComplexity,
  isStakes,
  nonBlankCriteria,
  toPayload,
  useDraftItems,
} from './PlanEditor.drafts'
import type { GradingProps, RowProps } from './PlanEditor.types'

const COMPLEXITY_OPTIONS = COMPLEXITY_VALUES.map((v) => ({ value: v, label: v }))
const STAKES_OPTIONS = STAKES_VALUES.map((v) => ({ value: v, label: v }))

// Mirror the backend field bounds (api/dto_plans.py) so an over-long or
// over-count edit is caught in the browser rather than after a 422 round trip.
const TITLE_MAX = 256
const TEXT_MAX = 8192
const MAX_ITEMS = 1000
const MAX_CRITERIA = 50

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
  const { drafts, change, remove, add } = useDraftItems(plan)
  const [saving, setSaving] = useState(false)

  const handleSave = useCallback(async () => {
    setSaving(true)
    const result = await usePlansStore
      .getState()
      .editPlan(plan.id, { items: drafts.map(toPayload) })
    setSaving(false)
    if (result) onDone()
  }, [plan.id, drafts, onDone])

  const { shown, choices, firstShown, pager, addAndFollow } = usePlanEditorRows(
    drafts,
    add,
  )

  // The backend requires every item to carry a title, at least one acceptance
  // criterion (capped at MAX_CRITERIA), a dispatchable owner or none, and, for
  // a work item, at least one expected deliverable. Gate the save on all of
  // them rather than surfacing the 422 after a round trip. Read over every
  // draft rather than the page: an item the operator has paged away from still
  // 422s the save, and a gate that could not see it would let them try.
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
      {shown.map((draft, offset) => (
        <PlanEditorRow
          key={draft.id}
          index={firstShown + offset}
          draft={draft}
          canRemove={drafts.length > 1}
          roster={roster}
          parentChoices={choices[offset] ?? []}
          onChange={change}
          onRemove={remove}
        />
      ))}
      {pager !== undefined && <Pagination {...pager} />}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={addAndFollow}
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
