import { useCallback, useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { X, Loader2, Calendar, GitBranch, User, Tag, Layers } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InlineEdit } from '@/components/ui/inline-edit'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import { Avatar } from '@/components/ui/avatar'
import { springDefault, overlayBackdrop, tweenExitFast } from '@/lib/motion'
import { getTaskStatusLabel, getTaskTypeLabel, getAvailableTransitions, getPriorityLabel } from '@/utils/tasks'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatDate, formatCurrency } from '@/utils/format'
import { useToastStore } from '@/stores/toast'
import type { Priority, TaskStatus } from '@/api/types/enums'
import type { CancelTaskRequest, Task, TransitionTaskRequest, UpdateTaskRequest } from '@/api/types/tasks'

export interface TaskDetailPanelProps {
  task: Task
  onClose: () => void
  onUpdate: (taskId: string, data: UpdateTaskRequest) => Promise<void>
  onTransition: (taskId: string, data: TransitionTaskRequest) => Promise<void>
  /** Resolves to ``true`` on success, ``false`` on failure (sentinel). */
  onCancel: (taskId: string, data: CancelTaskRequest) => Promise<boolean>
  /** Resolves to ``true`` on success, ``false`` on failure (sentinel). */
  onDelete: (taskId: string) => Promise<boolean>
  loading?: boolean
}

const PANEL_VARIANTS = {
  initial: { x: '100%', opacity: 0 },
  animate: { x: 0, opacity: 1, transition: springDefault },
  exit: { x: '100%', opacity: 0, transition: tweenExitFast },
}

const PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low']

export function TaskDetailPanel({
  task,
  onClose,
  onUpdate,
  onTransition,
  onCancel,
  onDelete,
  loading,
}: TaskDetailPanelProps) {
  const [cancelOpen, setCancelOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [transitioning, setTransitioning] = useState<TaskStatus | null>(null)

  const availableTransitions = getAvailableTransitions(task.status)

  // Close panel on Escape key (skip when a dialog is open)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (document.querySelector('[role="alertdialog"], [role="dialog"][aria-modal="true"]')) return
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // The ``onTransition`` / ``onCancel`` / ``onDelete`` / ``onUpdate``
  // callbacks are thin wrappers around the tasks store's sentinel-
  // returning mutations (see ``TaskBoardPage``), which already emit
  // both the success and error toast. No try/catch here -- the
  // wrappers don't throw, and adding a caller-side error toast
  // would double up with the store's.
  const handleTransition = useCallback(async (targetStatus: TaskStatus) => {
    setTransitioning(targetStatus)
    try {
      await onTransition(task.id, { target_status: targetStatus, expected_version: task.version })
    } finally {
      setTransitioning(null)
    }
  }, [task.id, task.version, onTransition])

  const handleCancel = useCallback(async (): Promise<boolean> => {
    if (!cancelReason.trim()) {
      useToastStore.getState().add({ variant: 'error', title: 'Please provide a cancellation reason' })
      return false
    }
    const ok = await onCancel(task.id, { reason: cancelReason.trim() })
    if (!ok) return false
    setCancelReason('')
    return true
  }, [task.id, cancelReason, onCancel])

  const handleDelete = useCallback(async (): Promise<boolean> => {
    const ok = await onDelete(task.id)
    if (!ok) return false
    onClose()
    return true
  }, [task.id, onDelete, onClose])

  return (
    <>
      {/* Backdrop */}
      <motion.div
        className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
        variants={overlayBackdrop}
        initial="initial"
        animate="animate"
        exit="exit"
        onClick={onClose}
      />

      {/* Panel */}
      <motion.aside
        className="fixed top-0 right-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-border bg-base shadow-lg"
        variants={PANEL_VARIANTS}
        initial="initial"
        animate="animate"
        exit="exit"
        role="dialog"
        aria-label={`Task detail: ${task.title}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div className="flex items-center gap-2">
            <TaskStatusIndicator status={task.status} label />
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
            <X className="size-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-section-gap">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="size-6 animate-spin text-text-muted" />
            </div>
          )}

          {!loading && (
            <>
              {/* Title */}
              <InlineEdit
                value={task.title}
                onSave={async (value) => {
                  await onUpdate(task.id, { title: value, expected_version: task.version })
                }}
                validate={(v) => v.trim().length === 0 ? 'Title is required' : null}
                className="text-lg font-semibold"
              />

              {/* Description */}
              <div>
                <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Description</label>
                <InlineEdit
                  value={task.description}
                  onSave={async (value) => {
                    await onUpdate(task.id, { description: value, expected_version: task.version })
                  }}
                  className="mt-1 text-sm text-text-secondary"
                />
              </div>

              {/* Priority */}
              <div>
                <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Priority</label>
                <div className="mt-1 flex items-center gap-2">
                  <PriorityBadge priority={task.priority} />
                  <select
                    value={task.priority}
                    onChange={async (e) => {
                      await onUpdate(task.id, { priority: e.target.value as Priority, expected_version: task.version })
                    }}
                    className="h-7 rounded border border-border bg-surface px-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
                    aria-label="Change priority"
                  >
                    {PRIORITIES.map((p) => (
                      <option key={p} value={p}>{getPriorityLabel(p)}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Assignee */}
              <div>
                <div className="flex items-center gap-2">
                  <User className="size-4 text-text-muted" />
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Assignee</span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  {task.assigned_to && <Avatar name={task.assigned_to} size="sm" />}
                  <InlineEdit
                    value={task.assigned_to ?? ''}
                    onSave={async (value) => {
                      await onUpdate(task.id, { assigned_to: value.trim() || undefined, expected_version: task.version })
                    }}
                    className="text-sm"
                    placeholder="Unassigned"
                  />
                </div>
              </div>

              {/* Metadata grid */}
              <div className="grid grid-cols-2 gap-grid-gap rounded-lg border border-border p-card">
                <MetaField icon={Tag} label="Type" value={getTaskTypeLabel(task.type)} />
                <MetaField icon={Layers} label="Complexity" value={task.estimated_complexity} />
                <MetaField icon={Calendar} label="Created" value={formatDate(task.created_at)} />
                <MetaField icon={Calendar} label="Updated" value={formatDate(task.updated_at)} />
                {task.deadline && (
                  <MetaField icon={Calendar} label="Deadline" value={formatDate(task.deadline)} />
                )}
                {task.cost != null && (
                  <MetaField icon={Tag} label="Cost" value={formatCurrency(task.cost, DEFAULT_CURRENCY)} />
                )}
              </div>

              {/* Dependencies */}
              {task.dependencies.length > 0 && (
                <div>
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
                    <GitBranch className="size-3.5" />
                    Dependencies ({task.dependencies.length})
                  </div>
                  <ul className="mt-1.5 space-y-1">
                    {task.dependencies.map((depId) => (
                      <li key={depId} className="rounded border border-border px-2 py-1 font-mono text-xs text-text-secondary">
                        {depId}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Acceptance criteria */}
              {task.acceptance_criteria.length > 0 && (
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
                    Acceptance Criteria
                  </span>
                  <ul className="mt-1.5 space-y-1">
                    {task.acceptance_criteria.map((criterion, idx) => (
                      // eslint-disable-next-line @eslint-react/no-array-index-key -- criteria lack unique IDs; descriptions may duplicate
                      <li key={`${criterion.description}-${idx}`} className="flex items-start gap-2 text-xs text-text-secondary">
                        <span className={cn('mt-0.5 size-3.5 shrink-0 rounded border', criterion.met ? 'border-success bg-success/20' : 'border-border')} />
                        {criterion.description}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Transitions */}
              {availableTransitions.length > 0 && (
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
                    Transitions
                  </span>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {availableTransitions.map((targetStatus) => (
                      <Button
                        key={targetStatus}
                        size="sm"
                        variant="outline"
                        disabled={transitioning !== null}
                        onClick={() => handleTransition(targetStatus)}
                      >
                        {transitioning === targetStatus && <Loader2 className="mr-1 size-3 animate-spin" />}
                        {getTaskStatusLabel(targetStatus)}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer actions */}
        <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
          {task.status !== 'cancelled' && task.status !== 'completed' && (
            <Button variant="outline" size="sm" onClick={() => setCancelOpen(true)}>
              Cancel Task
            </Button>
          )}
          <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}>
            Delete
          </Button>
        </div>
      </motion.aside>

      {/* Cancel dialog */}
      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={(open) => { setCancelOpen(open); if (!open) setCancelReason('') }}
        title="Cancel Task"
        description="Are you sure? Please provide a reason for cancellation."
        confirmLabel="Cancel Task"
        variant="destructive"
        onConfirm={handleCancel}
      >
        <textarea
          value={cancelReason}
          onChange={(e) => setCancelReason(e.target.value)}
          placeholder="Reason for cancellation..."
          className="mt-2 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-foreground outline-none resize-y focus:ring-2 focus:ring-accent min-h-[60px]"
          aria-label="Cancellation reason"
        />
      </ConfirmDialog>

      {/* Delete dialog */}
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete Task"
        description="This action cannot be undone. The task and all associated data will be permanently deleted."
        confirmLabel="Delete"
        variant="destructive"
        onConfirm={handleDelete}
      />
    </>
  )
}

function MetaField({ icon: Icon, label, value }: { icon: typeof Tag; label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="mt-0.5 size-3.5 text-text-muted" aria-hidden="true" />
      <div>
        <span className="block text-[10px] text-text-muted">{label}</span>
        <span className="block text-xs capitalize text-foreground">{value}</span>
      </div>
    </div>
  )
}
