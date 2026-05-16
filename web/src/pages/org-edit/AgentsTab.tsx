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
import { SortableContext, useSortable, verticalListSortingStrategy, sortableKeyboardCoordinates, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Plus, Users } from 'lucide-react'
import type { AgentConfig } from '@/api/types/agents'
import type {
  CompanyConfig,
  CreateAgentOrgRequest,
  UpdateAgentOrgRequest,
} from '@/api/types/org'
import { toRuntimeStatus } from '@/utils/agents'
import { useToastStore } from '@/stores/toast'
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
  onUpdateAgent: (name: string, data: UpdateAgentOrgRequest) => Promise<AgentConfig | null>
  onDeleteAgent: (name: string) => Promise<boolean>
  onReorderAgents: (deptName: string, orderedIds: string[]) => Promise<boolean>
  optimisticReorderAgents: (deptName: string, orderedIds: string[]) => () => void
}

function SortableAgentItem({
  agent,
  onClick,
}: {
  agent: AgentConfig
  onClick: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: agent.id ?? agent.name,
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
        <SortableContext items={agents.map((a) => a.id ?? a.name)} strategy={verticalListSortingStrategy}>
          <StaggerGroup className="grid gap-grid-gap">
            {agents.map((agent) => (
              <StaggerItem key={agent.id ?? agent.name}>
                <SortableAgentItem
                  agent={agent}
                  onClick={() => onEditAgent(agent)}
                />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </SortableContext>
      )}
    </SectionCard>
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
  const [activeAgent, setActiveAgent] = useState<AgentConfig | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  // Group agents by department
  const agentsByDept = useMemo(() => {
    if (!config) return new Map<string, AgentConfig[]>()
    const map = new Map<string, AgentConfig[]>()
    for (const dept of config.departments) {
      map.set(dept.name, [])
    }
    for (const agent of config.agents) {
      const list = map.get(agent.department) ?? []
      list.push(agent)
      map.set(agent.department, list)
    }
    return map
  }, [config])

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveAgent(event.active.data.current?.agent ?? null)
  }, [])

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveAgent(null)
      const { active, over } = event
      if (!over || active.id === over.id || !config) return

      const draggedAgent = active.data.current?.agent as AgentConfig | undefined
      if (!draggedAgent) return

      const deptAgents = agentsByDept.get(draggedAgent.department)
      if (!deptAgents) return

      const oldIndex = deptAgents.findIndex((a) => (a.id ?? a.name) === active.id)
      const newIndex = deptAgents.findIndex((a) => (a.id ?? a.name) === over.id)
      if (oldIndex === -1 || newIndex === -1) return

      const reordered = arrayMove(deptAgents, oldIndex, newIndex)
      const orderedIds = reordered.map((a) => a.id ?? a.name)

      const rollback = optimisticReorderAgents(draggedAgent.department, orderedIds)
      try {
        await onReorderAgents(draggedAgent.department, orderedIds)
      } catch {
        rollback()
        useToastStore.getState().add({
          variant: 'error',
          title: 'Could not reorder agents',
          description:
            'The order may have changed. Refresh the page and try again.',
        })
      }
    },
    [config, agentsByDept, optimisticReorderAgents, onReorderAgents],
  )

  if (!config || (config.agents.length === 0 && config.departments.length === 0)) {
    return (
      <div className="space-y-section-gap">
        <div className="flex justify-end">
          <Button onClick={() => setCreateOpen(true)} disabled={saving}>
            <Plus className="mr-1.5 size-3.5" />
            Add Agent
          </Button>
        </div>
        <EmptyState
          icon={Users}
          title="No agents"
          description="Create your first agent to get started."
        />
        <AgentCreateDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          departments={config?.departments ?? []}
          onCreate={onCreateAgent}
        />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="flex justify-end">
        <Button onClick={() => setCreateOpen(true)} disabled={saving}>
          <Plus className="mr-1.5 size-3.5" />
          Add Agent
        </Button>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => setActiveAgent(null)}
      >
        {Array.from(agentsByDept.entries()).map(([deptName, agents]) => {
          const dept = config.departments.find((d) => d.name === deptName)
          return (
            <DepartmentAgentsSection
              key={deptName}
              displayName={dept?.display_name ?? deptName}
              agents={agents}
              onEditAgent={setEditAgent}
            />
          )
        })}

        <DragOverlay>
          {activeAgent && (
            <AgentCard
              name={activeAgent.name}
              role={activeAgent.role}
              department={activeAgent.department}
              status={toRuntimeStatus(activeAgent.status ?? 'active')}
              className="shadow-lg"
            />
          )}
        </DragOverlay>
      </DndContext>

      <AgentCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        departments={config.departments}
        onCreate={onCreateAgent}
      />

      <AgentEditDrawer
        open={editAgent !== null}
        onClose={() => setEditAgent(null)}
        agent={editAgent}
        departments={config.departments}
        onUpdate={onUpdateAgent}
        onDelete={onDeleteAgent}
        saving={saving}
      />
    </div>
  )
}
