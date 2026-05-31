import { Send } from 'lucide-react'
import { useState } from 'react'

import type { InterventionKind, SupersedeMode } from '@/api/types'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SelectField } from '@/components/ui/select-field'
import { TagInput } from '@/components/ui/tag-input'
import { useSteeringStore } from '@/stores/steering'

type SteerableKind = Extract<InterventionKind, 'hint' | 'redirect'>

const KIND_OPTIONS = [
  { value: 'hint', label: 'Hint (advisory)' },
  { value: 'redirect', label: 'Redirect (replan)' },
] as const satisfies readonly { value: SteerableKind; label: string }[]

const SUPERSEDE_OPTIONS = [
  { value: 'none', label: 'None -- leave existing work running' },
  { value: 'explicit', label: 'Explicit -- cancel the listed tasks now' },
  { value: 'propose', label: 'Propose -- suggest a set to review first' },
] as const satisfies readonly { value: SupersedeMode; label: string }[]

interface SupersedeControlsProps {
  mode: SupersedeMode
  onModeChange: (mode: SupersedeMode) => void
  taskIds: string[]
  onTaskIdsChange: (ids: string[]) => void
  disabled: boolean
}

function SupersedeControls({
  mode,
  onModeChange,
  taskIds,
  onTaskIdsChange,
  disabled,
}: SupersedeControlsProps) {
  return (
    <div className="space-y-3">
      <SelectField
        label="Supersede obsolete tasks"
        options={SUPERSEDE_OPTIONS}
        value={mode}
        onChange={(value) => onModeChange(value as SupersedeMode)}
        disabled={disabled}
        hint="Explicit cancels immediately; Propose returns a set for you to confirm."
      />
      {mode !== 'none' && (
        <div className="space-y-1.5">
          <span className="text-sm font-medium text-foreground">Task IDs</span>
          <TagInput
            value={taskIds}
            onChange={onTaskIdsChange}
            disabled={disabled}
            placeholder="Add a task ID and press Enter"
          />
        </div>
      )}
    </div>
  )
}

interface NarrowingFieldsProps {
  taskIds: string[]
  onTaskIdsChange: (ids: string[]) => void
  agentIds: string[]
  onAgentIdsChange: (ids: string[]) => void
  disabled: boolean
}

function NarrowingFields({
  taskIds,
  onTaskIdsChange,
  agentIds,
  onAgentIdsChange,
  disabled,
}: NarrowingFieldsProps) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1.5">
        <span className="text-sm font-medium text-foreground">
          Narrow to tasks (optional)
        </span>
        <TagInput
          value={taskIds}
          onChange={onTaskIdsChange}
          disabled={disabled}
          placeholder="Empty = project-wide"
        />
      </div>
      <div className="space-y-1.5">
        <span className="text-sm font-medium text-foreground">
          Narrow to agents (optional)
        </span>
        <TagInput
          value={agentIds}
          onChange={onAgentIdsChange}
          disabled={disabled}
          placeholder="Empty = every agent"
        />
      </div>
    </div>
  )
}

export interface SteeringIssueFormProps {
  projectId: string
}

export function SteeringIssueForm({ projectId }: SteeringIssueFormProps) {
  const issueDirective = useSteeringStore((s) => s.issueDirective)
  const [kind, setKind] = useState<SteerableKind>('redirect')
  const [text, setText] = useState('')
  const [narrowTaskIds, setNarrowTaskIds] = useState<string[]>([])
  const [narrowAgentIds, setNarrowAgentIds] = useState<string[]>([])
  const [supersedeMode, setSupersedeMode] = useState<SupersedeMode>('none')
  const [supersedeTaskIds, setSupersedeTaskIds] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = projectId.trim() !== '' && text.trim() !== '' && !submitting

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    const result = await issueDirective({
      project_id: projectId,
      kind,
      text: text.trim(),
      narrow_task_ids: narrowTaskIds,
      narrow_agent_ids: narrowAgentIds,
      supersede_task_ids: supersedeTaskIds,
      supersede_mode: supersedeMode,
    })
    setSubmitting(false)
    if (result !== null) setText('')
  }

  return (
    <div className="space-y-4">
      <SegmentedControl<SteerableKind>
        label="Directive kind"
        options={KIND_OPTIONS}
        value={kind}
        onChange={setKind}
      />
      <InputField
        label="Directive"
        multiline
        rows={2}
        placeholder="e.g. use Postgres not Mongo"
        value={text}
        onValueChange={setText}
        disabled={submitting}
        hint="Stored in the project brain and adopted at the next safe turn boundary."
      />
      <NarrowingFields
        taskIds={narrowTaskIds}
        onTaskIdsChange={setNarrowTaskIds}
        agentIds={narrowAgentIds}
        onAgentIdsChange={setNarrowAgentIds}
        disabled={submitting}
      />
      <SupersedeControls
        mode={supersedeMode}
        onModeChange={setSupersedeMode}
        taskIds={supersedeTaskIds}
        onTaskIdsChange={setSupersedeTaskIds}
        disabled={submitting}
      />
      <Button onClick={() => void submit()} disabled={!canSubmit}>
        <Send className="size-4" aria-hidden="true" />
        Issue directive
      </Button>
    </div>
  )
}
