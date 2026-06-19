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

function SubtaskRow({ index, draft, canRemove, onChange, onRemove }: SubtaskRowProps) {
  return (
    <div className="space-y-3 rounded-md border border-border p-card">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">Subtask {index + 1}</span>
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
        value={draft.title}
        onValueChange={(value) => {
          onChange(index, { title: value })
        }}
      />
      <InputField
        label="Description"
        multiline
        rows={2}
        value={draft.description}
        onValueChange={(value) => {
          onChange(index, { description: value })
        }}
      />
    </div>
  )
}

export interface TaskDecomposeFormProps {
  drafts: readonly SubtaskDraft[]
  submitting: boolean
  onChange: (index: number, patch: Partial<SubtaskDraft>) => void
  onRemove: (index: number) => void
  onAdd: () => void
  onSubmit: () => void
}

export function TaskDecomposeForm({
  drafts,
  submitting,
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
          <Button onClick={onSubmit} disabled={submitting}>
            <Workflow />
            {submitting ? 'Decomposing…' : 'Decompose'}
          </Button>
        </div>
      </div>
    </SectionCard>
  )
}
