import { useEffect, useRef } from 'react'
import { Plus, Minus, Move, Settings, Tag, Shuffle } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogCloseButton,
} from '@/components/ui/dialog'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { cn } from '@/lib/utils'
import type { EdgeChange as EdgeChangeType, MetadataChange, NodeChange as NodeChangeType } from '@/api/types/workflows'

const NODE_CHANGE_ICONS: Record<string, typeof Plus> = {
  added: Plus,
  removed: Minus,
  moved: Move,
  config_changed: Settings,
  label_changed: Tag,
  type_changed: Shuffle,
}

const NODE_CHANGE_COLORS: Record<string, string> = {
  added: 'text-success',
  removed: 'text-danger',
  moved: 'text-accent',
  config_changed: 'text-warning',
  label_changed: 'text-muted',
  type_changed: 'text-warning',
}

const EDGE_CHANGE_COLORS: Record<string, string> = {
  added: 'text-success',
  removed: 'text-danger',
  reconnected: 'text-accent',
  type_changed: 'text-warning',
  label_changed: 'text-muted',
}

export interface MetadataChangeRowProps {
  change: MetadataChange
}

export function MetadataChangeRow({ change }: MetadataChangeRowProps) {
  return (
    <div className="rounded-md bg-card p-card text-sm">
      <span className="font-medium text-foreground">{change.field}</span>
      :{' '}
      <span className="text-danger line-through">{change.old_value}</span>{' '}
      <span className="text-success">{change.new_value}</span>
    </div>
  )
}

export interface NodeChangeRowProps {
  change: NodeChangeType
}

export function NodeChangeRow({ change }: NodeChangeRowProps) {
  const Icon = NODE_CHANGE_ICONS[change.change_type] ?? Settings
  const color = NODE_CHANGE_COLORS[change.change_type] ?? 'text-muted'
  const label = change.change_type.replace(/_/g, ' ')

  return (
    <div className="flex items-center gap-2 rounded-md bg-card p-card text-sm">
      <Icon className={cn('size-3.5', color)} />
      <span className="font-medium text-foreground">{change.node_id}</span>
      <span className={cn('text-xs', color)}>{label}</span>
    </div>
  )
}

export interface EdgeChangeRowProps {
  change: EdgeChangeType
}

export function EdgeChangeRow({ change }: EdgeChangeRowProps) {
  const color = EDGE_CHANGE_COLORS[change.change_type] ?? 'text-muted'
  const label = change.change_type.replace(/_/g, ' ')

  return (
    <div className="flex items-center gap-2 rounded-md bg-card p-card text-sm">
      <span className="font-medium text-foreground">{change.edge_id}</span>
      <span className={cn('text-xs', color)}>{label}</span>
    </div>
  )
}

export function VersionDiffViewer() {
  const diffResult = useWorkflowEditorStore((s) => s.diffResult)
  const clearDiff = useWorkflowEditorStore((s) => s.clearDiff)
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (clearTimerRef.current) clearTimeout(clearTimerRef.current)
  }, [])

  return (
    <Dialog
      open={diffResult !== null}
      onOpenChange={(open) => {
        if (clearTimerRef.current) {
          clearTimeout(clearTimerRef.current)
          clearTimerRef.current = null
        }
        if (!open) {
          // Delay clearing so the Base UI Dialog exit animation can finish
          // before diffResult content is unmounted.
          clearTimerRef.current = setTimeout(() => clearDiff(), 150)
        }
      }}
    >
      <DialogContent>
        {diffResult && (
          <>
            {/* Header */}
            <DialogHeader>
              <div>
                <DialogTitle>Version Diff</DialogTitle>
                <DialogDescription>
                  v{diffResult.from_version} to v{diffResult.to_version}
                </DialogDescription>
              </div>
              <DialogCloseButton />
            </DialogHeader>

            {/* Summary */}
            <div className="border-b border-border p-card">
              <p className="text-sm text-muted">{diffResult.summary}</p>
            </div>

            {/* Changes list */}
            <div className="flex-1 overflow-y-auto p-card">
              {/* Metadata changes */}
              {diffResult.metadata_changes.length > 0 && (
                <section className="mb-4">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                    Metadata
                  </h3>
                  <div className="flex flex-col gap-1">
                    {diffResult.metadata_changes.map((mc) => (
                      <MetadataChangeRow key={mc.field} change={mc} />
                    ))}
                  </div>
                </section>
              )}

              {/* Node changes */}
              {diffResult.node_changes.length > 0 && (
                <section className="mb-4">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                    Node Changes
                  </h3>
                  <div className="flex flex-col gap-1">
                    {diffResult.node_changes.map((nc) => (
                      <NodeChangeRow
                        key={`${nc.node_id}-${nc.change_type}`}
                        change={{
                          node_id: nc.node_id,
                          change_type: nc.change_type,
                          old_value: nc.old_value ?? null,
                          new_value: nc.new_value ?? null,
                        }}
                      />
                    ))}
                  </div>
                </section>
              )}

              {/* Edge changes */}
              {diffResult.edge_changes.length > 0 && (
                <section>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
                    Edge Changes
                  </h3>
                  <div className="flex flex-col gap-1">
                    {diffResult.edge_changes.map((ec) => (
                      <EdgeChangeRow
                        key={`${ec.edge_id}-${ec.change_type}`}
                        change={{
                          edge_id: ec.edge_id,
                          change_type: ec.change_type,
                          old_value: ec.old_value ?? null,
                          new_value: ec.new_value ?? null,
                        }}
                      />
                    ))}
                  </div>
                </section>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
