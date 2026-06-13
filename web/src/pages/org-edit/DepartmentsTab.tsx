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
import {
  SortableContext,
  useSortable,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Building2, PackagePlus, Plus, Users } from 'lucide-react'
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
import { DepartmentCreateDialog } from './DepartmentCreateDialog'
import { DepartmentEditDrawer } from './DepartmentEditDrawer'
import { PackSelectionDialog } from './PackSelectionDialog'
import { deriveBudget } from './department-budget'
import { BudgetTotalChip, BudgetWarningAlert } from './budget-warnings'

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

/** The agent/team/budget metadata row inside a department card. */
function DepartmentCardMeta({
  agentCount,
  teamCount,
  budgetPercent,
}: {
  agentCount: number
  teamCount: number
  budgetPercent: number | null | undefined
}) {
  const hasBudget = typeof budgetPercent === 'number' && budgetPercent > 0
  return (
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
      {hasBudget && (
        <>
          <span aria-hidden="true" className="text-border">
            &middot;
          </span>
          <span>{budgetPercent}% budget</span>
        </>
      )}
    </div>
  )
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
  const title = dept.display_name ?? dept.name
  // The runtime utilization gauge lives on the Org Chart / Dashboard;
  // this editor card only shows configuration metadata.
  return (
    <div ref={setNodeRef} style={style} {...(disabled ? {} : { ...attributes, ...listeners })}>
      <button
        type="button"
        className="w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-lg"
        onClick={onClick}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={`Edit department ${title}`}
      >
        <SectionCard title={title} icon={Building2}>
          <DepartmentCardMeta
            agentCount={agentCount}
            teamCount={dept.teams.length}
            budgetPercent={dept.budget_percent}
          />
        </SectionCard>
      </button>
    </div>
  )
}

interface DeptDragReorder {
  sensors: ReturnType<typeof useSensors>
  activeDept: Department | null
  handleDragStart: (event: DragStartEvent) => void
  handleDragEnd: (event: DragEndEvent) => Promise<void>
  clearActive: () => void
}

/** Reordered department-name list for a drag-end event, or null. */
function reorderDeptsFromEvent(event: DragEndEvent, config: CompanyConfig): string[] | null {
  const { active, over } = event
  if (!over || active.id === over.id) return null
  const oldIndex = config.departments.findIndex((d) => d.name === active.id)
  const newIndex = config.departments.findIndex((d) => d.name === over.id)
  if (oldIndex === -1 || newIndex === -1) return null
  return arrayMove([...config.departments], oldIndex, newIndex).map((d) => d.name)
}

function useDeptDragReorder(
  config: CompanyConfig | null,
  optimisticReorderDepartments: DepartmentsTabProps['optimisticReorderDepartments'],
  onReorderDepartments: DepartmentsTabProps['onReorderDepartments'],
): DeptDragReorder {
  const [activeDept, setActiveDept] = useState<Department | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveDept((event.active.data.current?.['dept'] as Department | undefined) ?? null)
  }, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveDept(null)
      if (!config) return
      const orderedNames = reorderDeptsFromEvent(event, config)
      if (!orderedNames) return
      const rollback = optimisticReorderDepartments(orderedNames)
      // onReorderDepartments returns false on failure (store owns the
      // toast); roll back when the store reports failure.
      const ok = await onReorderDepartments(orderedNames)
      if (!ok) rollback()
    },
    [config, optimisticReorderDepartments, onReorderDepartments],
  )

  const clearActive = useCallback(() => setActiveDept(null), [])

  return { sensors, activeDept, handleDragStart, handleDragEnd, clearActive }
}

function resolveEditHealth(
  editDept: Department | null,
  healthMap: Map<string, DepartmentHealth>,
): DepartmentHealth | null {
  return editDept ? (healthMap.get(editDept.name) ?? null) : null
}

function DepartmentsToolbar({
  onAddPack,
  onAddDept,
  disabled,
}: {
  onAddPack: () => void
  onAddDept: () => void
  disabled: boolean
}) {
  return (
    <div className="flex gap-2">
      <Button variant="outline" onClick={onAddPack} disabled={disabled}>
        <PackagePlus className="mr-1.5 size-3.5" />
        Add Team Pack
      </Button>
      <Button onClick={onAddDept} disabled={disabled}>
        <Plus className="mr-1.5 size-3.5" />
        Add Department
      </Button>
    </div>
  )
}

interface DepartmentsDndBoardProps {
  config: CompanyConfig
  drag: DeptDragReorder
  getAgentCount: (deptName: string) => number
  onEditDept: (dept: Department) => void
}

function DepartmentsDndBoard({ config, drag, getAgentCount, onEditDept }: DepartmentsDndBoardProps) {
  return (
    <DndContext
      sensors={drag.sensors}
      collisionDetection={closestCorners}
      onDragStart={drag.handleDragStart}
      onDragEnd={drag.handleDragEnd}
      onDragCancel={drag.clearActive}
    >
      <SortableContext items={config.departments.map((d) => d.name)} strategy={rectSortingStrategy}>
        <StaggerGroup className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
          {config.departments.map((dept) => (
            <StaggerItem key={dept.name}>
              <SortableDepartmentCard
                dept={dept}
                agentCount={getAgentCount(dept.name)}
                onClick={() => onEditDept(dept)}
              />
            </StaggerItem>
          ))}
        </StaggerGroup>
      </SortableContext>

      <DragOverlay>
        {drag.activeDept && (
          <div className="rounded-lg border border-accent bg-card p-card shadow-card-hover">
            <p className="text-sm font-semibold text-foreground">
              {drag.activeDept.display_name ?? drag.activeDept.name}
            </p>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}

function DepartmentsContent({
  props,
  drag,
  getAgentCount,
  editDept,
  setEditDept,
  editHealth,
}: {
  props: DepartmentsTabProps
  drag: DeptDragReorder
  getAgentCount: (deptName: string) => number
  editDept: Department | null
  setEditDept: (dept: Department | null) => void
  editHealth: DepartmentHealth | null
}) {
  const { config, saving } = props
  if (!config || config.departments.length === 0) {
    return (
      <EmptyState
        icon={Building2}
        title="No departments"
        description="Create your first department to get started."
      />
    )
  }
  return (
    <>
      <DepartmentsDndBoard
        config={config}
        drag={drag}
        getAgentCount={getAgentCount}
        onEditDept={setEditDept}
      />
      <DepartmentEditDrawer
        open={editDept !== null}
        onClose={() => setEditDept(null)}
        department={editDept}
        health={editHealth}
        config={config}
        onUpdate={props.onUpdateDepartment}
        onDelete={props.onDeleteDepartment}
        onCreateTeam={props.onCreateTeam}
        onUpdateTeam={props.onUpdateTeam}
        onDeleteTeam={props.onDeleteTeam}
        onReorderTeams={props.onReorderTeams}
        saving={saving}
      />
    </>
  )
}

export function DepartmentsTab(props: DepartmentsTabProps) {
  const { config, departmentHealths, saving, onCreateDepartment } = props
  const [createOpen, setCreateOpen] = useState(false)
  const [packOpen, setPackOpen] = useState(false)
  const [editDept, setEditDept] = useState<Department | null>(null)
  const drag = useDeptDragReorder(config, props.optimisticReorderDepartments, props.onReorderDepartments)

  const healthMap = useMemo(
    () => new Map(departmentHealths.map((h) => [h.department_name, h])),
    [departmentHealths],
  )
  const getAgentCount = useCallback(
    (deptName: string): number =>
      config ? config.agents.filter((a) => a.department === deptName).length : 0,
    [config],
  )

  // Compute the budget total before the empty check so the hook is
  // unconditional. `budget_percent` is a loose convention (no backend
  // validation that departments sum to 100), so the add flows can push
  // the total off 100% -- surface it via the chip + warning banner.
  const budgetTotal = useMemo(
    () =>
      (config?.departments ?? []).reduce(
        (sum, d) => sum + (typeof d.budget_percent === 'number' ? d.budget_percent : 0),
        0,
      ),
    [config?.departments],
  )

  const isEmpty = !config || config.departments.length === 0
  const budget = deriveBudget(budgetTotal)
  const editHealth = resolveEditHealth(editDept, healthMap)

  return (
    <div className="space-y-section-gap">
      <div className={cn('flex items-center gap-3', isEmpty ? 'justify-end' : 'justify-between')}>
        {!isEmpty && <BudgetTotalChip budget={budget} />}
        <DepartmentsToolbar
          onAddPack={() => setPackOpen(true)}
          onAddDept={() => setCreateOpen(true)}
          disabled={saving}
        />
      </div>

      {!isEmpty && <BudgetWarningAlert budget={budget} budgetTotal={budgetTotal} />}

      <DepartmentsContent
        props={props}
        drag={drag}
        getAgentCount={getAgentCount}
        editDept={editDept}
        setEditDept={setEditDept}
        editHealth={editHealth}
      />

      <DepartmentCreateDialog open={createOpen} onOpenChange={setCreateOpen} onCreate={onCreateDepartment} />

      <PackSelectionDialog open={packOpen} onOpenChange={setPackOpen} disabled={saving} />
    </div>
  )
}
