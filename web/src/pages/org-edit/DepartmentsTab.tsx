import { useCallback, useMemo, useState } from 'react'
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { SortableContext, useSortable, rectSortingStrategy, sortableKeyboardCoordinates, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { AlertTriangle, Building2, PackagePlus, Plus, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { DepartmentHealth } from '@/api/types/analytics'
import type {
  CompanyConfig,
  CreateDepartmentRequest,
  CreateTeamRequest,
  Department,
  TeamConfig,
  UpdateDepartmentRequest,
  UpdateTeamRequest,
} from '@/api/types/org'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useToastStore } from '@/stores/toast'
import { DepartmentCreateDialog } from './DepartmentCreateDialog'
import { DepartmentEditDrawer } from './DepartmentEditDrawer'
import { PackSelectionDialog } from './PackSelectionDialog'

export interface DepartmentsTabProps {
  config: CompanyConfig | null
  departmentHealths: readonly DepartmentHealth[]
  saving: boolean
  onCreateDepartment: (data: CreateDepartmentRequest) => Promise<Department | null>
  onUpdateDepartment: (name: string, data: UpdateDepartmentRequest) => Promise<Department | null>
  onDeleteDepartment: (name: string) => Promise<boolean>
  onReorderDepartments: (orderedNames: string[]) => Promise<boolean>
  optimisticReorderDepartments: (orderedNames: string[]) => () => void
  onCreateTeam: (deptName: string, data: CreateTeamRequest) => Promise<TeamConfig | null>
  onUpdateTeam: (deptName: string, teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  onDeleteTeam: (deptName: string, teamName: string, reassignTo?: string) => Promise<boolean>
  onReorderTeams: (deptName: string, orderedNames: string[]) => Promise<boolean>
}

function SortableDepartmentCard({
  dept,
  agentCount,
  onClick,
  disabled,
}: {
  dept: Department
  agentCount: number
  onClick: () => void
  disabled?: boolean
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: dept.name,
    data: { dept },
    disabled,
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  // Edit-time metadata shown on the card.  The previous design also
  // rendered a runtime `utilization_percent` gauge via DeptHealthBar,
  // but a live health bar is out of place on an editor surface -- the
  // user is here to configure the department, not to monitor it.  The
  // gauge remains on the Org Chart and Dashboard pages where it is
  // actually actionable.
  const teamCount = dept.teams.length
  const budgetPercent = dept.budget_percent

  return (
    <div ref={setNodeRef} style={style} {...(disabled ? {} : { ...attributes, ...listeners })}>
      <button
        type="button"
        className="w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-lg"
        onClick={onClick}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={`Edit department ${dept.display_name ?? dept.name}`}
      >
        <SectionCard title={dept.display_name ?? dept.name} icon={Building2}>
          <div className="flex flex-wrap items-center gap-3 text-sm text-text-secondary">
            <span className="inline-flex items-center gap-1.5">
              <Users className="size-3.5" aria-hidden="true" />
              {agentCount} agent{agentCount === 1 ? '' : 's'}
            </span>
            {teamCount > 0 && (
              <>
                <span aria-hidden="true" className="text-border">
                  &middot;
                </span>
                <span>
                  {teamCount} team{teamCount === 1 ? '' : 's'}
                </span>
              </>
            )}
            {typeof budgetPercent === 'number' && budgetPercent > 0 && (
              <>
                <span aria-hidden="true" className="text-border">
                  &middot;
                </span>
                <span>{budgetPercent}% budget</span>
              </>
            )}
          </div>
        </SectionCard>
      </button>
    </div>
  )
}

export function DepartmentsTab({
  config,
  departmentHealths,
  saving,
  onCreateDepartment,
  onUpdateDepartment,
  onDeleteDepartment,
  onReorderDepartments,
  optimisticReorderDepartments,
  onCreateTeam,
  onUpdateTeam,
  onDeleteTeam,
  onReorderTeams,
}: DepartmentsTabProps) {
  const [createOpen, setCreateOpen] = useState(false)
  const [packOpen, setPackOpen] = useState(false)
  const [editDept, setEditDept] = useState<Department | null>(null)
  const [activeDept, setActiveDept] = useState<Department | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const healthMap = useMemo(
    () => new Map(departmentHealths.map((h) => [h.department_name, h])),
    [departmentHealths],
  )

  const getAgentCount = useCallback(
    (deptName: string): number => {
      if (!config) return 0
      return config.agents.filter((a) => a.department === deptName).length
    },
    [config],
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveDept(event.active.data.current?.dept ?? null)
  }, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveDept(null)
      const { active, over } = event
      if (!over || active.id === over.id || !config) return

      const oldIndex = config.departments.findIndex((d) => d.name === active.id)
      const newIndex = config.departments.findIndex((d) => d.name === over.id)
      if (oldIndex === -1 || newIndex === -1) return

      const reordered = arrayMove([...config.departments], oldIndex, newIndex)
      const orderedNames = reordered.map((d) => d.name)

      const rollback = optimisticReorderDepartments(orderedNames)
      try {
        await onReorderDepartments(orderedNames)
      } catch {
        rollback()
        useToastStore.getState().add({ variant: 'error', title: 'Failed to reorder departments' })
      }
    },
    [config, optimisticReorderDepartments, onReorderDepartments],
  )

  const editHealth = editDept ? (healthMap.get(editDept.name) ?? null) : null

  // Total budget allocation across departments.  `budget_percent` is a
  // loose convention on Department (no backend validation that the sum
  // equals 100), so the Add Team / Add Department flows can silently
  // push the total above 100% -- e.g. an Add Team pack that appends an
  // 8% `security` department onto an already-100% org leaves the total
  // at 108.  We compute it here (before the early return so the hook
  // is unconditional) and surface a warning banner + running total
  // chip below so the miscount is visible at a glance.
  const budgetTotal = useMemo(
    () =>
      (config?.departments ?? []).reduce(
        (sum, d) => sum + (typeof d.budget_percent === 'number' ? d.budget_percent : 0),
        0,
      ),
    [config?.departments],
  )

  if (!config || config.departments.length === 0) {
    return (
      <div className="space-y-section-gap">
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setPackOpen(true)} disabled={saving}>
            <PackagePlus className="mr-1.5 size-3.5" />
            Add Team Pack
          </Button>
          <Button onClick={() => setCreateOpen(true)} disabled={saving}>
            <Plus className="mr-1.5 size-3.5" />
            Add Department
          </Button>
        </div>
        <EmptyState
          icon={Building2}
          title="No departments"
          description="Create your first department to get started."
        />
        <DepartmentCreateDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          onCreate={onCreateDepartment}
        />
        <PackSelectionDialog open={packOpen} onOpenChange={setPackOpen} disabled={saving} />
      </div>
    )
  }

  const budgetTotalRounded = Math.round(budgetTotal * 10) / 10
  const budgetIsOver = budgetTotal > 100.01
  const budgetIsUnder = budgetTotal < 99.99
  const budgetOff = budgetIsOver || budgetIsUnder

  return (
    <div className="space-y-section-gap">
      <div className="flex items-center justify-between gap-3">
        <div
          className={cn(
            'inline-flex items-center gap-2 rounded-md border px-card py-1 text-compact font-medium',
            budgetIsOver && 'border-danger/40 bg-danger/5 text-danger',
            budgetIsUnder && 'border-warning/40 bg-warning/5 text-warning',
            !budgetOff && 'border-border bg-card text-text-secondary',
          )}
          role="status"
          aria-live="polite"
        >
          <span>Total budget allocated: {budgetTotalRounded}%</span>
          {budgetOff && (
            <AlertTriangle className="size-3.5" aria-hidden="true" />
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setPackOpen(true)} disabled={saving}>
            <PackagePlus className="mr-1.5 size-3.5" />
            Add Team Pack
          </Button>
          <Button onClick={() => setCreateOpen(true)} disabled={saving}>
            <Plus className="mr-1.5 size-3.5" />
            Add Department
          </Button>
        </div>
      </div>
      {budgetOff && (
        <div
          role="alert"
          className={cn(
            'flex items-start gap-3 rounded-lg border p-card text-sm',
            budgetIsOver
              ? 'border-danger/40 bg-danger/5 text-danger'
              : 'border-warning/40 bg-warning/5 text-warning',
          )}
        >
          <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
          <div className="flex-1">
            {budgetIsOver ? (
              <>
                <div className="font-semibold">
                  Department budgets sum to {budgetTotalRounded}% (over 100%).
                </div>
                <p className="mt-1 text-compact text-danger/80">
                  This usually happens after adding a team pack or a new
                  department without rebalancing the existing allocations.
                  Open the departments below and reduce their budget percents
                  so the total is 100%.
                </p>
              </>
            ) : (
              <>
                <div className="font-semibold">
                  Department budgets sum to {budgetTotalRounded}% (under 100%).
                </div>
                <p className="mt-1 text-compact text-warning/80">
                  The remaining {Math.round((100 - budgetTotal) * 10) / 10}% is
                  unallocated. Increase one of the departments below or add a
                  new one to cover the gap.
                </p>
              </>
            )}
          </div>
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => setActiveDept(null)}
      >
        <SortableContext items={config.departments.map((d) => d.name)} strategy={rectSortingStrategy}>
          <StaggerGroup className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
            {config.departments.map((dept) => (
              <StaggerItem key={dept.name}>
                <SortableDepartmentCard
                  dept={dept}
                  agentCount={getAgentCount(dept.name)}
                  onClick={() => setEditDept(dept)}
                />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </SortableContext>

        <DragOverlay>
          {activeDept && (
            <div className="rounded-lg border border-accent bg-card p-card" style={{ boxShadow: 'var(--so-shadow-card-hover)' }}>
              <p className="text-sm font-semibold text-foreground">{activeDept.display_name ?? activeDept.name}</p>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      <DepartmentCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={onCreateDepartment}
      />

      <DepartmentEditDrawer
        open={editDept !== null}
        onClose={() => setEditDept(null)}
        department={editDept}
        health={editHealth}
        config={config}
        onUpdate={onUpdateDepartment}
        onDelete={onDeleteDepartment}
        onCreateTeam={onCreateTeam}
        onUpdateTeam={onUpdateTeam}
        onDeleteTeam={onDeleteTeam}
        onReorderTeams={onReorderTeams}
        saving={saving}
      />

      <PackSelectionDialog open={packOpen} onOpenChange={setPackOpen} disabled={saving} />
    </div>
  )
}
