import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Button } from '@/components/ui/button'
import { WORKFLOW_TYPES } from '@/utils/constants'
import { formatLabel } from '@/utils/format'
import { BlueprintPicker } from './BlueprintPicker'
import { useWorkflowCreateForm } from './useWorkflowCreateForm'

interface WorkflowCreateDrawerProps {
  open: boolean
  onClose: () => void
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
  const ctrl = useWorkflowCreateForm(onClose)

  return (
    <Drawer open={open} onClose={ctrl.handleClose} title="Create Workflow">
      <div className="flex flex-col gap-section-gap">
        <SegmentedControl
          label="Creation mode"
          value={ctrl.mode}
          onChange={ctrl.handleModeChange}
          options={MODE_OPTIONS}
          size="sm"
        />

        {ctrl.mode === 'blueprint' && (
          <BlueprintPicker
            selectedBlueprint={ctrl.selectedBlueprint}
            onSelect={ctrl.handleBlueprintSelect}
          />
        )}

        <InputField
          label="Name"
          value={ctrl.form.name}
          onChange={(e) => ctrl.updateField('name', e.target.value)}
          error={ctrl.errors.name}
          placeholder="Workflow name"
        />

        <InputField
          label="Description"
          value={ctrl.form.description}
          onChange={(e) => ctrl.updateField('description', e.target.value)}
          multiline
          placeholder="Optional description"
        />

        {ctrl.mode === 'blank' && (
          <SelectField
            label="Workflow Type"
            value={ctrl.form.workflowType}
            onChange={(val) => ctrl.updateField('workflowType', val)}
            options={WORKFLOW_TYPE_OPTIONS}
          />
        )}

        {ctrl.mode === 'blueprint' && ctrl.blueprintError && (
          <div
            role="alert"
            aria-live="assertive"
            className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger"
          >
            {ctrl.blueprintError}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={ctrl.handleClose} disabled={ctrl.submitting}>
            Cancel
          </Button>
          <Button onClick={ctrl.handleSubmit} disabled={ctrl.submitting}>
            {ctrl.submitting ? 'Creating...' : 'Create Workflow'}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
