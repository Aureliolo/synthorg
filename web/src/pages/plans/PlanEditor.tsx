import { useCallback, useState } from 'react'

import { Plus, Trash2 } from 'lucide-react'

import type { EditPlanRequest, Plan, PlanItem } from '@/api/types'
import { COMPLEXITY_VALUES, STAKES_VALUES } from '@/api/types/enum-values.gen'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { usePlansStore } from '@/stores/plans'

const COMPLEXITY_OPTIONS = COMPLEXITY_VALUES.map((v) => ({ value: v, label: v }))
const STAKES_OPTIONS = STAKES_VALUES.map((v) => ({ value: v, label: v }))

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
    acceptance_criteria: draft.acceptanceCriteria,
    expected_artifacts: draft.expectedArtifacts,
    required_skills: draft.requiredSkills,
    required_tags: draft.requiredTags,
    estimated_complexity: draft.complexity,
    stakes: draft.stakes,
  }
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
  }
}

interface RowProps {
  index: number
  draft: DraftItem
  canRemove: boolean
  onChange: (index: number, patch: Partial<DraftItem>) => void
  onRemove: (index: number) => void
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
        onValueChange={(value) => onChange(index, { title: value })}
      />
      <InputField
        label="Description"
        multiline
        rows={2}
        value={draft.description}
        onValueChange={(value) => onChange(index, { description: value })}
      />
      <InputField
        label="Owner (role or agent)"
        value={draft.owner}
        onValueChange={(value) => onChange(index, { owner: value })}
      />
      <div className="grid grid-cols-2 gap-grid-gap">
        <SelectField
          label="Complexity"
          options={COMPLEXITY_OPTIONS}
          value={draft.complexity}
          onChange={(value) =>
            onChange(index, { complexity: value as DraftItem['complexity'] })
          }
        />
        <SelectField
          label="Stakes"
          options={STAKES_OPTIONS}
          value={draft.stakes}
          onChange={(value) =>
            onChange(index, { stakes: value as DraftItem['stakes'] })
          }
        />
      </div>
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

  const canSave = drafts.length > 0 && drafts.every((d) => d.title.trim() !== '')

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
        <Button variant="outline" size="sm" onClick={handleAdd}>
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
