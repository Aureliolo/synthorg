import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Trash2, Users } from 'lucide-react'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CeremonyPolicyConfig } from '@/api/types/ceremony-policy'
import type {
  CompanyConfig,
  CreateTeamRequest,
  Department,
  TeamConfig,
  UpdateDepartmentRequest,
  UpdateTeamRequest,
} from '@/api/types/org'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Button } from '@/components/ui/button'
import { DepartmentCeremonyOverride } from './DepartmentCeremonyOverride'
import { TeamListSection } from './TeamListSection'
import { useDrawerDelete } from './use-drawer-delete'

export interface DepartmentEditDrawerProps {
  open: boolean
  onClose: () => void
  department: Department | null
  health: DepartmentHealth | null
  config: CompanyConfig | null
  onUpdate: (name: string, data: UpdateDepartmentRequest) => Promise<Department | null>
  onDelete: (name: string) => Promise<boolean>
  onCreateTeam: (deptName: string, data: CreateTeamRequest) => Promise<TeamConfig | null>
  onUpdateTeam: (deptName: string, teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  onDeleteTeam: (deptName: string, teamName: string, reassignTo?: string) => Promise<boolean>
  onReorderTeams: (deptName: string, orderedNames: string[]) => Promise<boolean>
  saving: boolean
}

interface DepartmentEditForm {
  budgetPercent: string
  setBudgetPercent: (value: string) => void
  ceremonyPolicy: CeremonyPolicyConfig | null
  setCeremonyPolicy: (policy: CeremonyPolicyConfig | null) => void
  submitError: string | null
  projectedTotal: number
  budgetWouldExceed: boolean
  deleteOpen: boolean
  setDeleteOpen: (open: boolean) => void
  deleting: boolean
  handleSave: () => Promise<void>
  handleDelete: () => Promise<void>
}

function useDepartmentEditForm(props: DepartmentEditDrawerProps): DepartmentEditForm {
  const { department, config, onUpdate, onDelete, onClose } = props
  const [budgetPercent, setBudgetPercent] = useState('0')
  const [ceremonyPolicy, setCeremonyPolicy] = useState<CeremonyPolicyConfig | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const del = useDrawerDelete(department?.name, onDelete, onClose)
  const { setDeleteOpen, setDeleting } = del

  const prevDepartmentRef = useRef<typeof department | undefined>(undefined)
  useEffect(() => {
    if (department !== prevDepartmentRef.current) {
      prevDepartmentRef.current = department
      if (department) {
        setBudgetPercent(department.budget_percent != null ? String(department.budget_percent) : '0')
        setCeremonyPolicy(department.ceremony_policy ?? null)
        setSubmitError(null)
      }
      setDeleteOpen(false)
      setDeleting(false)
    }
  }, [department, setDeleteOpen, setDeleting])

  const otherDeptsBudget = useMemo(() => {
    if (!config) return 0
    return config.departments
      .filter((d) => d.name !== department?.name)
      .reduce((sum, d) => sum + (d.budget_percent ?? 0), 0)
  }, [config, department?.name])

  const projectedTotal = otherDeptsBudget + (Number(budgetPercent) || 0)

  const handleSave = useCallback(async () => {
    if (!department) return
    setSubmitError(null)
    const pct = Number(budgetPercent)
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      setSubmitError('Budget percent must be between 0 and 100')
      return
    }
    // `autonomy_level` is intentionally omitted: this drawer only edits
    // budget and ceremony policy, so sending null would wipe a value
    // managed by the dedicated agent autonomy editor on every save.
    const result = await onUpdate(department.name, {
      budget_percent: pct,
      ceremony_policy: ceremonyPolicy as Record<string, unknown> | null,
    })
    // Store owns the toast; close only on success.
    if (result !== null) onClose()
  }, [department, budgetPercent, ceremonyPolicy, onUpdate, onClose])

  return {
    budgetPercent,
    setBudgetPercent,
    ceremonyPolicy,
    setCeremonyPolicy,
    submitError,
    projectedTotal,
    budgetWouldExceed: projectedTotal > 100.01,
    deleteOpen: del.deleteOpen,
    setDeleteOpen,
    deleting: del.deleting,
    handleSave,
    handleDelete: del.handleDelete,
  }
}

function DepartmentBudgetHint({
  projectedTotal,
  budgetWouldExceed,
}: {
  projectedTotal: number
  budgetWouldExceed: boolean
}) {
  if (budgetWouldExceed) {
    return (
      <p className="text-xs text-danger">
        Total would be {projectedTotal.toFixed(1)}% -- exceeds 100%.
      </p>
    )
  }
  if (projectedTotal < 99.99) {
    return (
      <p className="text-xs text-warning">
        Total would be {projectedTotal.toFixed(1)}% -- under-allocated.
      </p>
    )
  }
  return null
}

interface DepartmentEditBodyProps {
  department: Department
  health: DepartmentHealth | null
  saving: boolean
  form: DepartmentEditForm
  onClose: () => void
  onCreateTeam: DepartmentEditDrawerProps['onCreateTeam']
  onUpdateTeam: DepartmentEditDrawerProps['onUpdateTeam']
  onDeleteTeam: DepartmentEditDrawerProps['onDeleteTeam']
  onReorderTeams: DepartmentEditDrawerProps['onReorderTeams']
}

function DepartmentEditBody({
  department,
  health,
  saving,
  form,
  onClose,
  onCreateTeam,
  onUpdateTeam,
  onDeleteTeam,
  onReorderTeams,
}: DepartmentEditBodyProps) {
  const agentCount = health?.agent_count ?? 0
  return (
    <div className="space-y-5">
      {/* The runtime utilization gauge lives on Dashboard / Org Chart;
          this editor surface only configures the department. */}
      <div className="inline-flex items-center gap-1.5 text-compact text-text-secondary">
        <Users className="size-3.5" aria-hidden="true" />
        {agentCount} agent{agentCount === 1 ? '' : 's'}
      </div>

      <InputField
        label="Budget %"
        type="number"
        value={form.budgetPercent}
        onChange={(e) => form.setBudgetPercent(e.target.value)}
        hint="Percentage of company budget (0-100)"
      />

      <DepartmentCeremonyOverride
        policy={form.ceremonyPolicy}
        onChange={form.setCeremonyPolicy}
        disabled={saving}
      />

      <DepartmentBudgetHint
        projectedTotal={form.projectedTotal}
        budgetWouldExceed={form.budgetWouldExceed}
      />

      <TeamListSection
        teams={department.teams}
        saving={saving}
        onCreateTeam={(data) => onCreateTeam(department.name, data)}
        onUpdateTeam={(teamName, data) => onUpdateTeam(department.name, teamName, data)}
        onDeleteTeam={(teamName, reassignTo) => onDeleteTeam(department.name, teamName, reassignTo)}
        onReorderTeams={(names) => onReorderTeams(department.name, names)}
      />

      {form.submitError && <p className="text-xs text-danger">{form.submitError}</p>}

      <div className="flex items-center justify-between pt-2">
        <Button
          variant="outline"
          onClick={() => form.setDeleteOpen(true)}
          className="text-danger hover:text-danger"
          disabled={saving}
          data-testid="dept-delete"
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

export function DepartmentEditDrawer(props: DepartmentEditDrawerProps) {
  const { open, onClose, department, health, saving } = props
  const form = useDepartmentEditForm(props)
  const drawerTitle = department
    ? `Edit: ${department.display_name ?? department.name}`
    : 'Edit Department'

  return (
    <>
      <Drawer open={open} onClose={onClose} title={drawerTitle}>
        {department && (
          <DepartmentEditBody
            department={department}
            health={health}
            saving={saving}
            form={form}
            onClose={onClose}
            onCreateTeam={props.onCreateTeam}
            onUpdateTeam={props.onUpdateTeam}
            onDeleteTeam={props.onDeleteTeam}
            onReorderTeams={props.onReorderTeams}
          />
        )}
      </Drawer>

      <ConfirmDialog
        open={form.deleteOpen}
        onOpenChange={form.setDeleteOpen}
        title={`Delete ${department?.display_name ?? department?.name ?? 'department'}?`}
        description="This will remove the department and unassign all its agents."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={form.handleDelete}
        loading={form.deleting}
      />
    </>
  )
}
