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
  verticalListSortingStrategy,
  sortableKeyboardCoordinates,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Plus, Users } from 'lucide-react'
import type { AgentConfig } from '@/api/types/agents'
import type {
  CompanyConfig,
  CreateAgentOrgRequest,
  UpdateAgentOrgRequest,
} from '@/api/types/org'
import { toRuntimeStatus } from '@/utils/agents'
import { AgentCard } from '@/components/ui/agent-card'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { AgentCreateDialog } from './AgentCreateDialog'
import { AgentEditDrawer } from './AgentEditDrawer'

export interface AgentsTabProps {
  config: CompanyConfig | null
  saving: boolean
  onCreateAgent: (data: CreateAgentOrgRequest) => Promise<AgentConfig | null>
  onUpdateAgent: (agentId: string, data: UpdateAgentOrgRequest) => Promise<AgentConfig | null>
  onDeleteAgent: (agentId: string) => Promise<boolean>
  onReorderAgents: (deptName: string, orderedIds: string[]) => Promise<boolean>
  optimisticReorderAgents: (deptName: string, orderedIds: string[]) => () => void
}

type AgentsByDept = Map<string, AgentConfig[]>

function SortableAgentItem({ agent, onClick }: { agent: AgentConfig; onClick: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: agent.id,
    data: { agent },
  })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }
  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <button
        type="button"
        className="w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-lg"
        onClick={onClick}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={`Edit agent ${agent.name}`}
      >
        <AgentCard
          name={agent.name}
          role={agent.role}
          department={agent.department}
          status={toRuntimeStatus(agent.status ?? 'active')}
        />
      </button>
    </div>
  )
}

function DepartmentAgentsSection({
  displayName,
  agents,
  onEditAgent,
}: {
  displayName: string
  agents: AgentConfig[]
  onEditAgent: (agent: AgentConfig) => void
}) {
  return (
    <SectionCard
      title={displayName}
      icon={Users}
      action={
        <span className="text-xs text-text-secondary">
          {agents.length} agent{agents.length !== 1 ? 's' : ''}
        </span>
      }
    >
      {agents.length === 0 ? (
        <p className="py-4 text-center text-sm text-text-secondary">No agents in this department</p>
      ) : (
        <SortableContext
          items={agents.map((a) => a.id)}
          strategy={verticalListSortingStrategy}
        >
          <StaggerGroup className="grid gap-grid-gap">
            {agents.map((agent) => (
              <StaggerItem key={agent.id}>
                <SortableAgentItem agent={agent} onClick={() => onEditAgent(agent)} />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </SortableContext>
      )}
    </SectionCard>
  )
}

/** Group agents by department, seeding an empty bucket per department. */
function useAgentsByDept(config: CompanyConfig | null): AgentsByDept {
  return useMemo(() => {
    const map: AgentsByDept = new Map()
    if (!config) return map
    for (const dept of config.departments) map.set(dept.name, [])
    for (const agent of config.agents) {
      const list = map.get(agent.department) ?? []
      list.push(agent)
      map.set(agent.department, list)
    }
    return map
  }, [config])
}

/** Resolve the reordered id list for a drag-end event, or null. */
function reorderFromDragEvent(
  event: DragEndEvent,
  agentsByDept: AgentsByDept,
): { department: string; orderedIds: string[] } | null {
  const { active, over } = event
  if (!over || active.id === over.id) return null
  const draggedAgent = active.data.current?.agent as AgentConfig | undefined
  if (!draggedAgent) return null
  const deptAgents = agentsByDept.get(draggedAgent.department)
  if (!deptAgents) return null
  const oldIndex = deptAgents.findIndex((a) => a.id === active.id)
  const newIndex = deptAgents.findIndex((a) => a.id === over.id)
  if (oldIndex === -1 || newIndex === -1) return null
  const reordered = arrayMove(deptAgents, oldIndex, newIndex)
  return { department: draggedAgent.department, orderedIds: reordered.map((a) => a.id) }
}

interface AgentDragReorder {
  sensors: ReturnType<typeof useSensors>
  activeAgent: AgentConfig | null
  handleDragStart: (event: DragStartEvent) => void
  handleDragEnd: (event: DragEndEvent) => Promise<void>
  clearActive: () => void
}

function useAgentDragReorder(
  config: CompanyConfig | null,
  agentsByDept: AgentsByDept,
  optimisticReorderAgents: AgentsTabProps['optimisticReorderAgents'],
  onReorderAgents: AgentsTabProps['onReorderAgents'],
): AgentDragReorder {
  const [activeAgent, setActiveAgent] = useState<AgentConfig | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveAgent((event.active.data.current?.agent as AgentConfig | undefined) ?? null)
  }, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveAgent(null)
      if (!config) return
      const result = reorderFromDragEvent(event, agentsByDept)
      if (!result) return
      const rollback = optimisticReorderAgents(result.department, result.orderedIds)
      // onReorderAgents returns false on failure (store owns the toast);
      // roll back when the store reports failure.
      const ok = await onReorderAgents(result.department, result.orderedIds)
      if (!ok) rollback()
    },
    [config, agentsByDept, optimisticReorderAgents, onReorderAgents],
  )

  const clearActive = useCallback(() => setActiveAgent(null), [])

  return { sensors, activeAgent, handleDragStart, handleDragEnd, clearActive }
}

function agentsTabIsEmpty(config: CompanyConfig | null): boolean {
  return !config || (config.agents.length === 0 && config.departments.length === 0)
}

interface AgentsDndBoardProps {
  config: CompanyConfig
  agentsByDept: AgentsByDept
  drag: AgentDragReorder
  onEditAgent: (agent: AgentConfig) => void
}

function AgentsDndBoard({ config, agentsByDept, drag, onEditAgent }: AgentsDndBoardProps) {
  return (
    <DndContext
      sensors={drag.sensors}
      collisionDetection={closestCorners}
      onDragStart={drag.handleDragStart}
      onDragEnd={drag.handleDragEnd}
      onDragCancel={drag.clearActive}
    >
      {Array.from(agentsByDept.entries()).map(([deptName, agents]) => {
        const dept = config.departments.find((d) => d.name === deptName)
        return (
          <DepartmentAgentsSection
            key={deptName}
            displayName={dept?.display_name ?? deptName}
            agents={agents}
            onEditAgent={onEditAgent}
          />
        )
      })}

      <DragOverlay>
        {drag.activeAgent && (
          <AgentCard
            name={drag.activeAgent.name}
            role={drag.activeAgent.role}
            department={drag.activeAgent.department}
            status={toRuntimeStatus(drag.activeAgent.status ?? 'active')}
            className="shadow-lg"
          />
        )}
      </DragOverlay>
    </DndContext>
  )
}

export function AgentsTab({
  config,
  saving,
  onCreateAgent,
  onUpdateAgent,
  onDeleteAgent,
  onReorderAgents,
  optimisticReorderAgents,
}: AgentsTabProps) {
  const [createOpen, setCreateOpen] = useState(false)
  const [editAgent, setEditAgent] = useState<AgentConfig | null>(null)
  const agentsByDept = useAgentsByDept(config)
  const drag = useAgentDragReorder(config, agentsByDept, optimisticReorderAgents, onReorderAgents)

  const isEmpty = agentsTabIsEmpty(config)

  return (
    <div className="space-y-section-gap">
      <div className="flex justify-end">
        <Button onClick={() => setCreateOpen(true)} disabled={saving}>
          <Plus className="mr-1.5 size-3.5" />
          Add Agent
        </Button>
      </div>

      {config && !isEmpty ? (
        <AgentsDndBoard
          config={config}
          agentsByDept={agentsByDept}
          drag={drag}
          onEditAgent={setEditAgent}
        />
      ) : (
        <EmptyState icon={Users} title="No agents" description="Create your first agent to get started." />
      )}

      <AgentCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        departments={config?.departments ?? []}
        onCreate={onCreateAgent}
      />

      {!isEmpty && config && (
        <AgentEditDrawer
          open={editAgent !== null}
          onClose={() => setEditAgent(null)}
          agent={editAgent}
          departments={config.departments}
          onUpdate={onUpdateAgent}
          onDelete={onDeleteAgent}
          saving={saving}
        />
      )}
    </div>
  )
}
