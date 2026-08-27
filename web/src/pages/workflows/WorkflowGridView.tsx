import { Workflow } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { WorkflowCard } from './WorkflowCard'
import type { ConfirmHandler } from '@/components/ui/confirm-dialog'
import type { WorkflowDefinition } from '@/api/types/workflows'

interface WorkflowGridViewProps {
  workflows: readonly WorkflowDefinition[]
  onDelete: ConfirmHandler<[string]>
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
        title="No workflows yet"
        // Reached only with NO workflows: the page renders its own
        // filtered-to-nothing state first, so naming filters here pointed an
        // operator with none set at something they cannot adjust.
        description="Create a workflow to give the org a repeatable sequence of steps to run."
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
