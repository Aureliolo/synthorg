import { Plus, Trash2, Workflow } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import type { SubtaskDraft } from './useTaskDecomposeController'

interface SubtaskRowProps {
  index: number
  draft: SubtaskDraft
  canRemove: boolean
  onChange: (index: number, patch: Partial<SubtaskDraft>) => void
  onRemove: (index: number) => void
}

interface SubtaskDoneFieldsProps {
  index: number
  draft: SubtaskDraft
  onChange: (index: number, patch: Partial<SubtaskDraft>) => void
}

function SubtaskDoneFields({ index, draft, onChange }: SubtaskDoneFieldsProps) {
  return (
    <div className="grid gap-grid-gap sm:grid-cols-2">
      <InputField
        label="Acceptance criteria"
        multiline
        rows={2}
        required
        value={draft.acceptanceCriteria}
        hint="One per line; what makes this subtask done."
        onValueChange={(value) => {
          onChange(index, { acceptanceCriteria: value })
        }}
      />
      <InputField
        label="Expected deliverables"
        multiline
        rows={2}
        required
        value={draft.expectedArtifacts}
        hint="One per line; a subtask that declares none is rejected."
        onValueChange={(value) => {
          onChange(index, { expectedArtifacts: value })
        }}
      />
    </div>
  )
}

function SubtaskRow({ index, draft, canRemove, onChange, onRemove }: SubtaskRowProps) {
  return (
    <div className="space-y-3 rounded-md border border-border p-card">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-foreground">Subtask {index + 1}</h3>
        {canRemove && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove subtask ${String(index + 1)}`}
            onClick={() => {
              onRemove(index)
            }}
          >
            <Trash2 />
          </Button>
        )}
      </div>
      <div className="grid gap-grid-gap sm:grid-cols-2">
        <InputField
          label="Label"
          value={draft.label}
          hint="Unique within this plan; referenced by dependencies."
          onValueChange={(value) => {
            onChange(index, { label: value })
          }}
        />
        <InputField
          label="Dependencies"
          value={draft.dependencies}
          hint="Comma-separated labels this subtask depends on."
          onValueChange={(value) => {
            onChange(index, { dependencies: value })
          }}
        />
      </div>
      <InputField
        label="Title"
        required
        value={draft.title}
        onValueChange={(value) => {
          onChange(index, { title: value })
        }}
      />
      <InputField
        label="Description"
        multiline
        rows={2}
        required
        value={draft.description}
        onValueChange={(value) => {
          onChange(index, { description: value })
        }}
      />
      <SubtaskDoneFields index={index} draft={draft} onChange={onChange} />
    </div>
  )
}

export interface TaskDecomposeFormProps {
  drafts: readonly SubtaskDraft[]
  submitting: boolean
  /** False while any subtask is missing a field the backend requires. */
  canSubmit?: boolean
  onChange: (index: number, patch: Partial<SubtaskDraft>) => void
  onRemove: (index: number) => void
  onAdd: () => void
  onSubmit: () => void
}

export function TaskDecomposeForm({
  drafts,
  submitting,
  canSubmit = true,
  onChange,
  onRemove,
  onAdd,
  onSubmit,
}: TaskDecomposeFormProps) {
  return (
    <SectionCard title="Manual decomposition" icon={Workflow}>
      <div className="space-y-section-gap">
        <p className="text-sm text-muted-foreground">
          Author the subtask breakdown by hand; it is validated and classified
          on submit.
        </p>
        {drafts.map((draft, index) => (
          <SubtaskRow
            key={draft.key}
            index={index}
            draft={draft}
            canRemove={drafts.length > 1}
            onChange={onChange}
            onRemove={onRemove}
          />
        ))}
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" onClick={onAdd}>
            <Plus />
            Add subtask
          </Button>
          <Button onClick={onSubmit} disabled={submitting || !canSubmit}>
            <Workflow />
            {submitting ? 'Decomposing…' : 'Decompose'}
          </Button>
        </div>
      </div>
    </SectionCard>
  )
}
