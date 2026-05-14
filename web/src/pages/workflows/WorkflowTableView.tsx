import { Link, useNavigate } from 'react-router'
import { Workflow, MoreHorizontal, Copy, Trash2, Pencil } from 'lucide-react'
import { Menu } from '@base-ui/react/menu'
import { useState } from 'react'
import { ROUTES } from '@/router/routes'
import { EmptyState } from '@/components/ui/empty-state'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { formatDateTime } from '@/utils/format'
import type { WorkflowDefinition } from '@/api/types/workflows'

interface WorkflowTableViewProps {
  workflows: readonly WorkflowDefinition[]
  onDelete: (id: string) => void | Promise<void>
  onDuplicate: (id: string) => void
  onToggleSelect?: (id: string) => void
  selectedIds?: ReadonlySet<string>
}

export function WorkflowTableView({
  workflows,
  onDelete,
  onDuplicate,
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
          <thead>
            <tr className="border-b border-border bg-muted/50">
              {onToggleSelect && (
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
          <tbody>
            {workflows.map((w) => {
              const editorUrl = `${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(w.id)}`
              const isSelected = selectedIds?.has(w.id) ?? false
              return (
                <tr
                  key={w.id}
                  className={`border-b border-border last:border-0 transition-colors hover:bg-muted/30 ${isSelected ? 'bg-accent/5' : ''}`}
                >
                  {onToggleSelect && (
                    <td className="px-2 py-2.5">
                      <input
                        type="checkbox"
                        className="size-4 rounded border-border accent-accent"
                        checked={isSelected}
                        onChange={() => onToggleSelect(w.id)}
                        aria-label={`Select workflow ${w.name}`}
                      />
                    </td>
                  )}
                  <td className="px-4 py-2.5 font-medium text-foreground">
                    <Link
                      to={editorUrl}
                      className="block w-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                      aria-label={`Open workflow ${w.name}`}
                    >
                      {w.name}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
                      {(w.workflow_type ?? '').replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground">{w.nodes.length}</td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground">{w.edges.length}</td>
                  <td className="px-4 py-2.5 text-right text-muted-foreground">v{w.version}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{formatDateTime(w.updated_at)}</td>
                  <td className="px-2 py-2.5">
                    <Menu.Root>
                      <Menu.Trigger
                        render={
                          <button
                            type="button"
                            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                            aria-label={`Actions for ${w.name}`}
                          >
                            <MoreHorizontal className="size-4" />
                          </button>
                        }
                      />
                      <Menu.Portal>
                        <Menu.Positioner align="end" sideOffset={4}>
                          <Menu.Popup className="z-50 min-w-36 rounded-lg border border-border bg-card py-1 shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate,scale] duration-150 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95">
                            <Menu.Item
                              className="flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm text-foreground outline-none data-[highlighted]:bg-surface"
                              onClick={() => { void navigate(editorUrl) }}
                            >
                              <Pencil className="size-3.5" />
                              Edit
                            </Menu.Item>
                            <Menu.Item
                              className="flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm text-foreground outline-none data-[highlighted]:bg-surface"
                              onClick={() => onDuplicate(w.id)}
                            >
                              <Copy className="size-3.5" />
                              Duplicate
                            </Menu.Item>
                            <Menu.Item
                              className="flex w-full cursor-default items-center gap-2 px-3 py-1.5 text-sm text-danger outline-none data-[highlighted]:bg-surface"
                              onClick={() => setConfirmDeleteId(w.id)}
                            >
                              <Trash2 className="size-3.5" />
                              Delete
                            </Menu.Item>
                          </Menu.Popup>
                        </Menu.Positioner>
                      </Menu.Portal>
                    </Menu.Root>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={confirmDeleteId !== null}
        onOpenChange={(open) => { if (!open) setConfirmDeleteId(null) }}
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
