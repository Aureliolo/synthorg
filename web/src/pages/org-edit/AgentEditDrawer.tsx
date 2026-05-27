import { useCallback, useMemo, useRef, useState } from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import type { AgentConfig } from '@/api/types/agents'
import { SENIORITY_LEVEL_VALUES, type SeniorityLevel } from '@/api/types/enums'
import type { Department, UpdateAgentOrgRequest } from '@/api/types/org'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/status-badge'
import { formatDateOnly } from '@/utils/format'
import { toRuntimeStatus } from '@/utils/agents'
import { useDrawerDelete } from './use-drawer-delete'

export interface AgentEditDrawerProps {
  open: boolean
  onClose: () => void
  agent: AgentConfig | null
  departments: readonly Department[]
  onUpdate: (name: string, data: UpdateAgentOrgRequest) => Promise<AgentConfig | null>
  onDelete: (name: string) => Promise<boolean>
  saving: boolean
}

interface AgentFormState {
  name: string
  role: string
  department: string
  level: SeniorityLevel
}

const LEVEL_OPTIONS = SENIORITY_LEVEL_VALUES.map((l) => ({ value: l, label: l }))

/** "provider / model_id" display string for an agent's model config. */
function agentModelDisplay(agent: AgentConfig): string {
  return [
    typeof agent.model['provider'] === 'string' ? agent.model['provider'] : '',
    typeof agent.model['model_id'] === 'string' ? agent.model['model_id'] : '',
  ]
    .filter((v) => v.length > 0)
    .join(' / ')
}

interface AgentEditForm {
  form: AgentFormState
  setForm: React.Dispatch<React.SetStateAction<AgentFormState>>
  submitError: string | null
  deleteOpen: boolean
  setDeleteOpen: (open: boolean) => void
  deleting: boolean
  deptOptions: { value: string; label: string }[]
  hiredDate: string
  modelDisplay: string
  handleSave: () => Promise<void>
  handleDelete: () => Promise<void>
}

function useAgentEditForm(props: AgentEditDrawerProps): AgentEditForm {
  const { agent, departments, onUpdate, onDelete, onClose } = props
  const [form, setForm] = useState<AgentFormState>({
    name: '',
    role: '',
    department: '',
    level: 'mid',
  })
  const [submitError, setSubmitError] = useState<string | null>(null)
  const del = useDrawerDelete(agent?.name, onDelete, onClose)

  // Render-phase prop sync (the "adjust state when a prop changes"
  // pattern): reseed the form whenever a different agent is opened.
  const prevAgentRef = useRef<typeof agent | undefined>(undefined)
  if (agent !== prevAgentRef.current) {
    prevAgentRef.current = agent
    if (agent) {
      setForm({ name: agent.name, role: agent.role, department: agent.department, level: agent.level })
      setSubmitError(null)
    }
    del.setDeleteOpen(false)
    del.setDeleting(false)
  }

  const deptOptions = useMemo(
    () => departments.map((d) => ({ value: d.name, label: d.display_name ?? d.name })),
    [departments],
  )
  const hiredDate = useMemo(
    () => (agent?.hiring_date ? formatDateOnly(agent.hiring_date) : ''),
    [agent],
  )
  const modelDisplay = useMemo(() => (agent ? agentModelDisplay(agent) : ''), [agent])

  const handleSave = useCallback(async () => {
    if (!agent) return
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      setSubmitError('Name is required')
      return
    }
    setSubmitError(null)
    const result = await onUpdate(agent.name, {
      name: trimmedName,
      role: form.role.trim() || undefined,
      department: form.department as UpdateAgentOrgRequest['department'],
      level: form.level,
    })
    // Store owns the error toast; the drawer only decides whether to close.
    if (result !== null) onClose()
  }, [agent, form, onUpdate, onClose])

  return {
    form,
    setForm,
    submitError,
    deleteOpen: del.deleteOpen,
    setDeleteOpen: del.setDeleteOpen,
    deleting: del.deleting,
    deptOptions,
    hiredDate,
    modelDisplay,
    handleSave,
    handleDelete: del.handleDelete,
  }
}

interface AgentEditBodyProps {
  agent: AgentConfig
  saving: boolean
  form: AgentEditForm
  onClose: () => void
}

function AgentEditBody({ agent, saving, form, onClose }: AgentEditBodyProps) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <StatusBadge status={toRuntimeStatus(agent.status ?? 'active')} label />
        {form.hiredDate && <span className="text-xs text-text-secondary">Hired: {form.hiredDate}</span>}
      </div>

      <InputField
        label="Name"
        value={form.form.name}
        onChange={(e) => form.setForm((prev) => ({ ...prev, name: e.target.value }))}
      />
      <InputField
        label="Role"
        value={form.form.role}
        onChange={(e) => form.setForm((prev) => ({ ...prev, role: e.target.value }))}
      />
      <SelectField
        label="Department"
        options={form.deptOptions}
        value={form.form.department}
        onChange={(v) => form.setForm((prev) => ({ ...prev, department: v }))}
      />
      <SelectField
        label="Level"
        options={LEVEL_OPTIONS}
        value={form.form.level}
        onChange={(v) => form.setForm((prev) => ({ ...prev, level: v as SeniorityLevel }))}
      />

      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wider text-text-muted">Status</p>
        <StatusBadge status={toRuntimeStatus(agent.status ?? 'active')} />
      </div>

      <div className="border-t border-border pt-4 space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-text-muted">Model</p>
        {form.modelDisplay && (
          <p className="text-xs text-text-secondary font-mono">{form.modelDisplay}</p>
        )}
      </div>

      {form.submitError && <p className="text-xs text-danger">{form.submitError}</p>}

      <div className="flex items-center justify-between pt-2">
        <Button
          variant="outline"
          onClick={() => form.setDeleteOpen(true)}
          className="text-danger hover:text-danger"
          disabled={saving}
        >
          <Trash2 className="mr-1.5 size-3.5" />
          Delete
        </Button>
        <div className="flex gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={form.handleSave} disabled={saving}>
            {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
            Save
          </Button>
        </div>
      </div>
    </div>
  )
}

export function AgentEditDrawer(props: AgentEditDrawerProps) {
  const { open, onClose, agent, saving } = props
  const form = useAgentEditForm(props)

  return (
    <>
      <Drawer open={open} onClose={onClose} title={agent ? `Edit: ${agent.name}` : 'Edit Agent'}>
        {agent && <AgentEditBody agent={agent} saving={saving} form={form} onClose={onClose} />}
      </Drawer>

      <ConfirmDialog
        open={form.deleteOpen}
        onOpenChange={form.setDeleteOpen}
        title={`Delete ${agent?.name ?? 'agent'}?`}
        description="This action cannot be undone. The agent will be permanently removed."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={form.handleDelete}
        loading={form.deleting}
      />
    </>
  )
}
