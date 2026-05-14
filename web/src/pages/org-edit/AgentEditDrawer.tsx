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
import { getErrorMessage } from '@/utils/errors'
import { formatDateOnly } from '@/utils/format'
import { toRuntimeStatus } from '@/utils/agents'

export interface AgentEditDrawerProps {
  open: boolean
  onClose: () => void
  agent: AgentConfig | null
  departments: readonly Department[]
  onUpdate: (name: string, data: UpdateAgentOrgRequest) => Promise<AgentConfig>
  onDelete: (name: string) => Promise<void>
  saving: boolean
}

const LEVEL_OPTIONS = SENIORITY_LEVEL_VALUES.map((l) => ({ value: l, label: l }))

export function AgentEditDrawer({
  open,
  onClose,
  agent,
  departments,
  onUpdate,
  onDelete,
  saving,
}: AgentEditDrawerProps) {
  const [form, setForm] = useState({
    name: '',
    role: '',
    department: '',
    level: 'mid' as SeniorityLevel,
  })
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const prevAgentRef = useRef<typeof agent | undefined>(undefined)
  if (agent !== prevAgentRef.current) {
    prevAgentRef.current = agent
    if (agent) {
      setForm({
        name: agent.name,
        role: agent.role,
        department: agent.department,
        level: agent.level,
      })
      setSubmitError(null)
    }
    setDeleteOpen(false)
    setDeleting(false)
  }

  const deptOptions = useMemo(
    () => departments.map((d) => ({ value: d.name, label: d.display_name ?? d.name })),
    [departments],
  )
  const hiredDate = useMemo(
    () => agent?.hiring_date ? formatDateOnly(agent.hiring_date) : '',
    [agent],
  )
  const modelDisplay = useMemo(() => {
    if (!agent) return ''
    return [
      typeof agent.model['provider'] === 'string' ? agent.model['provider'] : '',
      typeof agent.model['model_id'] === 'string' ? agent.model['model_id'] : '',
    ].filter((v) => v.length > 0).join(' / ')
  }, [agent])

  const handleSave = useCallback(async () => {
    if (!agent) return
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      setSubmitError('Name is required')
      return
    }
    setSubmitError(null)
    try {
      await onUpdate(agent.name, {
        name: trimmedName,
        role: form.role.trim() || undefined,
        department: form.department as UpdateAgentOrgRequest['department'],
        level: form.level,
      })
      onClose()
    } catch (err) {
      setSubmitError(getErrorMessage(err))
    }
  }, [agent, form, onUpdate, onClose])

  const handleDelete = useCallback(async () => {
    if (!agent) return
    setDeleting(true)
    try {
      await onDelete(agent.name)
      setDeleteOpen(false)
      onClose()
    } catch (err) {
      setSubmitError(getErrorMessage(err))
    } finally {
      setDeleting(false)
    }
  }, [agent, onDelete, onClose])

  return (
    <>
      <Drawer open={open} onClose={onClose} title={agent ? `Edit: ${agent.name}` : 'Edit Agent'}>
        {agent && (
          <div className="space-y-5">
            <div className="flex items-center gap-2">
              <StatusBadge status={toRuntimeStatus(agent.status ?? 'active')} label />
              {hiredDate && <span className="text-xs text-text-secondary">Hired: {hiredDate}</span>}
            </div>

            <InputField
              label="Name"
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
            />

            <InputField
              label="Role"
              value={form.role}
              onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value }))}
            />

            <SelectField
              label="Department"
              options={deptOptions}
              value={form.department}
              onChange={(v) => setForm((prev) => ({ ...prev, department: v }))}
            />

            <SelectField
              label="Level"
              options={LEVEL_OPTIONS}
              value={form.level}
              onChange={(v) => setForm((prev) => ({ ...prev, level: v as SeniorityLevel }))}
            />

            {/* Status is read-only; managed by system */}
            <div className="space-y-1">
              <p className="text-xs font-semibold uppercase tracking-wider text-text-muted">Status</p>
              <StatusBadge status={toRuntimeStatus(agent?.status ?? 'active')} />
            </div>

            {/* Read-only info */}
            <div className="border-t border-border pt-4 space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-text-muted">Model</p>
              {modelDisplay && (
                <p className="text-xs text-text-secondary font-mono">{modelDisplay}</p>
              )}
            </div>

            {submitError && (
              <p className="text-xs text-danger">{submitError}</p>
            )}

            <div className="flex items-center justify-between pt-2">
              <Button
                variant="outline"
                onClick={() => setDeleteOpen(true)}
                className="text-danger hover:text-danger"
                disabled={saving}
              >
                <Trash2 className="mr-1.5 size-3.5" />
                Delete
              </Button>
              <div className="flex gap-3">
                <Button variant="outline" onClick={onClose}>Cancel</Button>
                <Button
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
                  Save
                </Button>
              </div>
            </div>
          </div>
        )}
      </Drawer>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={`Delete ${agent?.name ?? 'agent'}?`}
        description="This action cannot be undone. The agent will be permanently removed."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        loading={deleting}
      />
    </>
  )
}
