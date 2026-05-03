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
import { getErrorMessage } from '@/utils/errors'
import { DepartmentCeremonyOverride } from './DepartmentCeremonyOverride'
import { TeamListSection } from './TeamListSection'

export interface DepartmentEditDrawerProps {
  open: boolean
  onClose: () => void
  department: Department | null
  health: DepartmentHealth | null
  config: CompanyConfig | null
  onUpdate: (name: string, data: UpdateDepartmentRequest) => Promise<Department>
  onDelete: (name: string) => Promise<void>
  onCreateTeam: (deptName: string, data: CreateTeamRequest) => Promise<TeamConfig>
  onUpdateTeam: (deptName: string, teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig>
  onDeleteTeam: (deptName: string, teamName: string, reassignTo?: string) => Promise<void>
  onReorderTeams: (deptName: string, orderedNames: string[]) => Promise<void>
  saving: boolean
}

export function DepartmentEditDrawer({
  open,
  onClose,
  department,
  health,
  config,
  onUpdate,
  onDelete,
  onCreateTeam,
  onUpdateTeam,
  onDeleteTeam,
  onReorderTeams,
  saving,
}: DepartmentEditDrawerProps) {
  const [budgetPercent, setBudgetPercent] = useState('0')
  const [ceremonyPolicy, setCeremonyPolicy] = useState<CeremonyPolicyConfig | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

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

  }, [department])

  const otherDeptsBudget = useMemo(() => {
    if (!config) return 0
    return config.departments
      .filter((d) => d.name !== department?.name)
      .reduce((sum, d) => sum + (d.budget_percent ?? 0), 0)
  }, [config, department?.name])

  const projectedTotal = otherDeptsBudget + (Number(budgetPercent) || 0)
  const budgetWouldExceed = projectedTotal > 100.01

  const handleSave = useCallback(async () => {
    if (!department) return
    setSubmitError(null)
    const pct = Number(budgetPercent)
    if (Number.isFinite(pct) && (pct < 0 || pct > 100)) {
      setSubmitError('Budget percent must be between 0 and 100')
      return
    }
    try {
      await onUpdate(department.name, {
        budget_percent: Number.isFinite(pct) ? pct : undefined,
        ceremony_policy: ceremonyPolicy,
      })
      onClose()
    } catch (err) {
      setSubmitError(getErrorMessage(err))
    }
  }, [department, budgetPercent, ceremonyPolicy, onUpdate, onClose])

  const handleDelete = useCallback(async () => {
    if (!department) return
    setDeleting(true)
    try {
      await onDelete(department.name)
      setDeleteOpen(false)
      onClose()
    } catch (err) {
      setSubmitError(getErrorMessage(err))
    } finally {
      setDeleting(false)
    }
  }, [department, onDelete, onClose])

  return (
    <>
      <Drawer open={open} onClose={onClose} title={department ? `Edit: ${department.display_name ?? department.name}` : 'Edit Department'}>
        {department && (
          <div className="space-y-5">
            {/*
             * Previously the drawer opened with a runtime utilization
             * gauge (utilization_percent via DeptHealthBar).  It was
             * confusing on an editor surface because the user is here
             * to configure the department, not to monitor it.  The
             * live gauge remains on the Dashboard / Org Chart views.
             */}
            <div className="inline-flex items-center gap-1.5 text-compact text-text-secondary">
              <Users className="size-3.5" aria-hidden="true" />
              {(health?.agent_count ?? 0)} agent{(health?.agent_count ?? 0) === 1 ? '' : 's'}
            </div>

            <InputField
              label="Budget %"
              type="number"
              value={budgetPercent}
              onChange={(e) => setBudgetPercent(e.target.value)}
              hint="Percentage of company budget (0-100)"
            />

            {/* Ceremony policy override */}
            <DepartmentCeremonyOverride
              policy={ceremonyPolicy}
              onChange={setCeremonyPolicy}
              disabled={saving}
            />

            {budgetWouldExceed && (
              <p className="text-xs text-danger">
                Total would be {projectedTotal.toFixed(1)}% -- exceeds 100%.
              </p>
            )}
            {!budgetWouldExceed && projectedTotal < 99.99 && (
              <p className="text-xs text-warning">
                Total would be {projectedTotal.toFixed(1)}% -- under-allocated.
              </p>
            )}

            <TeamListSection
              teams={department.teams}
              saving={saving}
              onCreateTeam={(data) => onCreateTeam(department.name, data)}
              onUpdateTeam={(teamName, data) => onUpdateTeam(department.name, teamName, data)}
              onDeleteTeam={(teamName, reassignTo) => onDeleteTeam(department.name, teamName, reassignTo)}
              onReorderTeams={(names) => onReorderTeams(department.name, names)}
            />

            {submitError && (
              <p className="text-xs text-danger">{submitError}</p>
            )}

            <div className="flex items-center justify-between pt-2">
              <Button
                variant="outline"
                onClick={() => setDeleteOpen(true)}
                className="text-danger hover:text-danger"
                disabled={saving}
                data-testid="dept-delete"
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
        title={`Delete ${department?.display_name ?? department?.name ?? 'department'}?`}
        description="This will remove the department and unassign all its agents."
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={handleDelete}
        loading={deleting}
      />
    </>
  )
}
