import { Link, useNavigate, type NavigateFunction } from 'react-router'
import { Workflow, MoreHorizontal, Copy, Download, Trash2, Pencil } from 'lucide-react'
import { Menu } from '@base-ui/react/menu'
import { useState } from 'react'
import { ROUTES } from '@/router/routes'
import { EmptyState } from '@/components/ui/empty-state'
import { ConfirmDialog, type ConfirmHandler } from '@/components/ui/confirm-dialog'
import { formatDateTime } from '@/utils/format'
import type { WorkflowDefinition } from '@/api/types/workflows'

interface WorkflowTableViewProps {
  workflows: readonly WorkflowDefinition[]
  onDelete: (id: string) => ReturnType<ConfirmHandler>
  onDuplicate: (id: string) => void
  onExport: (id: string) => void | Promise<void>
  onToggleSelect?: (id: string) => void
  selectedIds?: ReadonlySet<string>
}

export function WorkflowTableView({
  workflows,
  onDelete,
  onDuplicate,
  onExport,
  onToggleSelect,
  selectedIds,
}: WorkflowTableViewProps) {
  const navigate = useNavigate()
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

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
    <>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm" role="table">
          <WorkflowTableHeader showSelect={Boolean(onToggleSelect)} />
          <tbody>
            {workflows.map((w) => (
              <WorkflowTableRow
                key={w.id}
                workflow={w}
                navigate={navigate}
                isSelected={selectedIds?.has(w.id) ?? false}
                onToggleSelect={onToggleSelect}
                onDuplicate={onDuplicate}
                onExport={() => void onExport(w.id)}
                onRequestDelete={() => setConfirmDeleteId(w.id)}
              />
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmDeleteId !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmDeleteId(null)
        }}
        onConfirm={() => {
          // Forward the promise so ConfirmDialog's onConfirm handler can
          // observe rejection and keep the dialog open for retry.
          if (confirmDeleteId) return onDelete(confirmDeleteId)
          return undefined
        }}
        title="Delete workflow"
        description="This action cannot be undone. The workflow definition will be permanently deleted."
        variant="destructive"
        confirmLabel="Delete"
      />
    </>
  )
}

interface WorkflowTableHeaderProps {
  showSelect: boolean
}

function WorkflowTableHeader({ showSelect }: WorkflowTableHeaderProps) {
  return (
    <thead>
      <tr className="border-b border-border bg-muted/50">
        {showSelect && (
          <th className="w-10 px-2 py-2">
            <span className="sr-only">Select workflow</span>
          </th>
        )}
        <th className="px-4 py-2 text-left font-medium text-muted-foreground">Name</th>
        <th className="px-4 py-2 text-left font-medium text-muted-foreground">Type</th>
        <th className="px-4 py-2 text-right font-medium text-muted-foreground">Nodes</th>
        <th className="px-4 py-2 text-right font-medium text-muted-foreground">Edges</th>
        <th className="px-4 py-2 text-right font-medium text-muted-foreground">Version</th>
        <th className="px-4 py-2 text-left font-medium text-muted-foreground">Updated</th>
        <th className="w-10 px-2 py-2" />
      </tr>
    </thead>
  )
}

interface WorkflowTableRowProps {
  workflow: WorkflowDefinition
  navigate: NavigateFunction
  isSelected: boolean
  onToggleSelect?: ((id: string) => void) | undefined
  onDuplicate: (id: string) => void
  onExport: () => void
  onRequestDelete: () => void
}

function WorkflowTableRow({
  workflow,
  navigate,
  isSelected,
  onToggleSelect,
  onDuplicate,
  onExport,
  onRequestDelete,
}: WorkflowTableRowProps) {
  const editorUrl = `${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(workflow.id)}`
  const rowClasses = `border-b border-border last:border-0 transition-colors hover:bg-muted/30 ${
    isSelected ? 'bg-accent/5' : ''
  }`
  return (
    <tr className={rowClasses}>
      {onToggleSelect && (
        <td className="px-2 py-2.5">
          <input
            type="checkbox"
            className="size-4 rounded border-border accent-accent"
            checked={isSelected}
            onChange={() => onToggleSelect(workflow.id)}
            aria-label={`Select workflow ${workflow.name}`}
          />
        </td>
      )}
      <td className="px-4 py-2.5 font-medium text-foreground">
        <Link
          to={editorUrl}
          className="block w-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          aria-label={`Open workflow ${workflow.name}`}
        >
          {workflow.name}
        </Link>
      </td>
      <td className="px-4 py-2.5">
        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
          {workflow.workflow_type.replace(/_/g, ' ')}
        </span>
      </td>
      <td className="px-4 py-2.5 text-right text-muted-foreground">{workflow.nodes.length}</td>
      <td className="px-4 py-2.5 text-right text-muted-foreground">{workflow.edges.length}</td>
      <td className="px-4 py-2.5 text-right text-muted-foreground">v{workflow.version}</td>
      <td className="px-4 py-2.5 text-muted-foreground">{formatDateTime(workflow.updated_at)}</td>
      <td className="px-2 py-2.5">
        <WorkflowRowMenu
          workflowName={workflow.name}
          editorUrl={editorUrl}
          onEdit={() => void navigate(editorUrl)}
          onDuplicate={() => onDuplicate(workflow.id)}
          onExport={onExport}
          onRequestDelete={onRequestDelete}
        />
      </td>
    </tr>
  )
}

const MENU_POPUP_CLASSES =
  'z-50 min-w-36 rounded-lg border border-border bg-card py-1 shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate,scale] duration-[var(--so-transition-fast)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95'

const MENU_ITEM_CLASSES =
  'flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm outline-none data-[highlighted]:bg-surface'

interface WorkflowRowMenuProps {
  workflowName: string
  editorUrl: string
  onEdit: () => void
  onDuplicate: () => void
  onExport: () => void
  onRequestDelete: () => void
}

function WorkflowRowMenu({
  workflowName,
  onEdit,
  onDuplicate,
  onExport,
  onRequestDelete,
}: WorkflowRowMenuProps) {
  return (
    <Menu.Root>
      <Menu.Trigger
        render={
          <button
            type="button"
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={`Actions for ${workflowName}`}
          >
            <MoreHorizontal className="size-4" />
          </button>
        }
      />
      <Menu.Portal>
        <Menu.Positioner align="end" sideOffset={4}>
          <Menu.Popup className={MENU_POPUP_CLASSES}>
            <Menu.Item className={`${MENU_ITEM_CLASSES} text-foreground`} onClick={onEdit}>
              <Pencil className="size-3.5" />
              Edit
            </Menu.Item>
            <Menu.Item className={`${MENU_ITEM_CLASSES} text-foreground`} onClick={onDuplicate}>
              <Copy className="size-3.5" />
              Duplicate
            </Menu.Item>
            <Menu.Item className={`${MENU_ITEM_CLASSES} text-foreground`} onClick={onExport}>
              <Download className="size-3.5" />
              Export YAML
            </Menu.Item>
            <Menu.Item className={`${MENU_ITEM_CLASSES} text-danger`} onClick={onRequestDelete}>
              <Trash2 className="size-3.5" />
              Delete
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}
