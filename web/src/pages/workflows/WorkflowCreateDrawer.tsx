import { useState } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Button } from '@/components/ui/button'
import { useWorkflowsStore } from '@/stores/workflows'
import { WORKFLOW_TYPES } from '@/utils/constants'
import { formatLabel } from '@/utils/format'
import { BlueprintPicker } from './BlueprintPicker'

interface WorkflowCreateDrawerProps {
  open: boolean
  onClose: () => void
}

type CreateMode = 'blank' | 'blueprint'

interface FormState {
  name: string
  description: string
  workflowType: string
}

const INITIAL_FORM: FormState = {
  name: '',
  description: '',
  workflowType: 'sequential_pipeline',
}

const MODE_OPTIONS = [
  { value: 'blank' as const, label: 'Blank' },
  { value: 'blueprint' as const, label: 'From Template' },
]

const WORKFLOW_TYPE_OPTIONS = WORKFLOW_TYPES.map((t) => ({
  value: t,
  label: formatLabel(t),
}))

export function WorkflowCreateDrawer({ open, onClose }: WorkflowCreateDrawerProps) {
  const [mode, setMode] = useState<CreateMode>('blank')
  const [selectedBlueprint, setSelectedBlueprint] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [blueprintError, setBlueprintError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const blueprints = useWorkflowsStore((s) => s.blueprints)

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  function handleBlueprintSelect(name: string | null) {
    setSelectedBlueprint(name)
    setBlueprintError(null)
    if (name) {
      const bp = blueprints.find((b) => b.name === name)
      if (bp) {
        setForm((prev) => ({
          ...prev,
          name: bp.display_name,
          description: bp.description,
          workflowType: bp.workflow_type,
        }))
      }
    }
  }

  async function handleSubmit() {
    if (mode === 'blueprint' && !selectedBlueprint) {
      setBlueprintError('Select a template or switch to Blank mode')
      return
    }

    const next: Partial<Record<keyof FormState, string>> = {}
    if (!form.name.trim()) next.name = 'Name is required'
    setErrors(next)
    if (Object.keys(next).length > 0) return

    setSubmitting(true)
    const created = mode === 'blueprint'
      ? await useWorkflowsStore.getState().createFromBlueprint({
          blueprint_name: selectedBlueprint!,
          name: form.name.trim(),
          description: form.description.trim() || undefined,
        })
      : await useWorkflowsStore.getState().createWorkflow({
          name: form.name.trim(),
          description: form.description.trim() ?? '',
          version: '1.0.0',
          workflow_type: form.workflowType as 'sequential_pipeline' | 'parallel_execution' | 'kanban' | 'agile_kanban',
          inputs: [],
          outputs: [],
          is_subworkflow: false,
          nodes: [],
          edges: [],
        })
    setSubmitting(false)
    if (created) handleClose()
  }

  function handleClose() {
    setMode('blank')
    setSelectedBlueprint(null)
    setForm(INITIAL_FORM)
    setErrors({})
    setBlueprintError(null)
    onClose()
  }

  return (
    <Drawer open={open} onClose={handleClose} title="Create Workflow">
      <div className="flex flex-col gap-section-gap">
        <SegmentedControl
          label="Creation mode"
          value={mode}
          onChange={(next) => {
            setMode(next)
            if (next !== 'blueprint') setBlueprintError(null)
          }}
          options={MODE_OPTIONS}
          size="sm"
        />

        {mode === 'blueprint' && (
          <BlueprintPicker
            selectedBlueprint={selectedBlueprint}
            onSelect={handleBlueprintSelect}
          />
        )}

        <InputField
          label="Name"
          value={form.name}
          onChange={(e) => updateField('name', e.target.value)}
          error={errors.name}
          placeholder="Workflow name"
        />

        <InputField
          label="Description"
          value={form.description}
          onChange={(e) => updateField('description', e.target.value)}
          multiline
          placeholder="Optional description"
        />

        {mode === 'blank' && (
          <SelectField
            label="Workflow Type"
            value={form.workflowType}
            onChange={(val) => updateField('workflowType', val)}
            options={WORKFLOW_TYPE_OPTIONS}
          />
        )}

        {mode === 'blueprint' && blueprintError && (
          <div role="alert" aria-live="assertive" className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger">
            {blueprintError}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Creating...' : 'Create Workflow'}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
