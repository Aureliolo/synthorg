import { Workflow } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { WorkflowCard } from './WorkflowCard'
import type { WorkflowDefinition } from '@/api/types/workflows'

interface WorkflowGridViewProps {
  workflows: readonly WorkflowDefinition[]
  onDelete: (id: string) => void
  onDuplicate: (id: string) => void
  onExport: (id: string) => void | Promise<void>
  /** When defined, renders selection checkboxes on each card. */
  onToggleSelect?: (id: string) => void
  /** Which ids are currently selected. */
  selectedIds?: ReadonlySet<string>
}

export function WorkflowGridView({
  workflows,
  onDelete,
  onDuplicate,
  onExport,
  onToggleSelect,
  selectedIds,
}: WorkflowGridViewProps) {
  if (workflows.length === 0) {
    return (
      <EmptyState
        icon={Workflow}
        title="No workflows found"
        description="Try adjusting your filters or create a new workflow."
      />
    )
  }

  return (
    <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 xl:grid-cols-3">
      {workflows.map((workflow) => (
        <StaggerItem key={workflow.id}>
          <WorkflowCard
            workflow={workflow}
            onDelete={onDelete}
            onDuplicate={onDuplicate}
            onExport={onExport}
            onToggleSelect={onToggleSelect}
            selected={selectedIds?.has(workflow.id)}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}
