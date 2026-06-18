import { useCallback, useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { X, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineEdit } from '@/components/ui/inline-edit'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { TaskStatusIndicator } from '@/components/ui/task-status-indicator'
import { springDefault, overlayBackdrop, tweenExitFast } from '@/lib/motion'
import { useToastStore } from '@/stores/toast'
import type { TaskStatus } from '@/api/types/enums'
import type {
  CancelTaskRequest,
  DashboardTask,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '@/api/types/tasks'

import {
  AcceptanceCriteriaList,
  AssigneeSection,
  DependenciesList,
  DescriptionEdit,
  MetaGrid,
  PrioritySection,
  TransitionsSection,
  TaskDetailPanelFooter,
} from './TaskDetailPanelSections'

export interface TaskDetailPanelProps {
  task: DashboardTask
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

function useEscapeToClose(onClose: () => void): void {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // A nested confirm dialog (cancel / delete) owns Escape while open; the
      // panel's own ``role="dialog"`` is deliberately excluded from this guard.
      if (document.querySelector('[role="alertdialog"]')) return
      onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])
}

export function TaskDetailPanel({
  task,
  onClose,
  onUpdate,
  onTransition,
  onCancel,
  onDelete,
  loading,
}: TaskDetailPanelProps) {
  const dialogs = useTaskDetailDialogs({ task, onCancel, onDelete, onClose })
  const [transitioning, setTransitioning] = useState<TaskStatus | null>(null)

  useEscapeToClose(onClose)

  const handleTransition = useCallback(
    async (targetStatus: TaskStatus) => {
      setTransitioning(targetStatus)
      try {
        await onTransition(task.id, {
          target_status: targetStatus,
          expected_version: task.version ?? null,
        })
      } finally {
        setTransitioning(null)
      }
    },
    [task.id, task.version, onTransition],
  )

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
        variants={overlayBackdrop}
        initial="initial"
        animate="animate"
        exit="exit"
        onClick={onClose}
      />
      <motion.aside
        className="fixed top-0 right-0 z-50 flex h-full w-[var(--so-drawer-width-default)] max-w-[100vw] flex-col border-l border-border bg-base shadow-[var(--so-shadow-card-hover)]"
        variants={PANEL_VARIANTS}
        initial="initial"
        animate="animate"
        exit="exit"
        role="dialog"
        aria-modal="true"
        aria-label={`Task detail: ${task.title}`}
      >
        <PanelHeader task={task} onClose={onClose} />
        <PanelBody
          loading={Boolean(loading)}
          task={task}
          onUpdate={onUpdate}
          onTransition={handleTransition}
          transitioning={transitioning}
        />
        <TaskDetailPanelFooter
          task={task}
          onCancelClick={dialogs.openCancel}
          onDeleteClick={dialogs.openDelete}
        />
      </motion.aside>
      <CancelConfirmDialog dialogs={dialogs} />
      <DeleteConfirmDialog dialogs={dialogs} />
    </>
  )
}

interface PanelHeaderProps {
  task: DashboardTask
  onClose: () => void
}

function PanelHeader({ task, onClose }: PanelHeaderProps) {
  return (
    <div className="flex items-center justify-between border-b border-border px-6 py-4">
      <div className="flex items-center gap-2">
        <TaskStatusIndicator status={task.status} label />
      </div>
      <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
        <X className="size-4" />
      </Button>
    </div>
  )
}

interface PanelBodyProps {
  loading: boolean
  task: DashboardTask
  onUpdate: (taskId: string, data: UpdateTaskRequest) => Promise<void>
  onTransition: (targetStatus: TaskStatus) => Promise<void>
  transitioning: TaskStatus | null
}

function PanelBody({
  loading,
  task,
  onUpdate,
  onTransition,
  transitioning,
}: PanelBodyProps) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-section-gap">
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : (
        <TaskDetailBody
          task={task}
          onUpdate={onUpdate}
          onTransition={onTransition}
          transitioning={transitioning}
        />
      )}
    </div>
  )
}

interface TaskDetailBodyProps {
  task: DashboardTask
  onUpdate: (taskId: string, data: UpdateTaskRequest) => Promise<void>
  onTransition: (targetStatus: TaskStatus) => Promise<void>
  transitioning: TaskStatus | null
}

function TaskDetailBody({
  task,
  onUpdate,
  onTransition,
  transitioning,
}: TaskDetailBodyProps) {
  return (
    <>
      <InlineEdit
        value={task.title}
        onSave={async (value) => {
          await onUpdate(task.id, { title: value, expected_version: task.version ?? null })
        }}
        validate={(v) => (v.trim().length === 0 ? 'Title is required' : null)}
        className="text-lg font-semibold"
      />
      <DescriptionEdit task={task} onUpdate={onUpdate} />
      <PrioritySection task={task} onUpdate={onUpdate} />
      <AssigneeSection task={task} onUpdate={onUpdate} />
      <MetaGrid task={task} />
      {task.dependencies.length > 0 && <DependenciesList task={task} />}
      {task.acceptance_criteria.length > 0 && <AcceptanceCriteriaList task={task} />}
      <TransitionsSection
        task={task}
        transitioning={transitioning}
        onTransition={onTransition}
      />
    </>
  )
}

interface UseTaskDetailDialogsArgs {
  task: DashboardTask
  onCancel: (taskId: string, data: CancelTaskRequest) => Promise<boolean>
  onDelete: (taskId: string) => Promise<boolean>
  onClose: () => void
}

interface TaskDetailDialogs {
  cancelOpen: boolean
  deleteOpen: boolean
  cancelReason: string
  openCancel: () => void
  openDelete: () => void
  setCancelOpen: (open: boolean) => void
  setDeleteOpen: (open: boolean) => void
  setCancelReason: (value: string) => void
  handleCancel: () => Promise<boolean>
  handleDelete: () => Promise<boolean>
}

function useTaskDetailDialogs({
  task,
  onCancel,
  onDelete,
  onClose,
}: UseTaskDetailDialogsArgs): TaskDetailDialogs {
  const [cancelOpen, setCancelOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')

  const handleCancel = useCallback(async (): Promise<boolean> => {
    if (!cancelReason.trim()) {
      useToastStore.getState().add({
        variant: 'error',
        title: 'Please provide a cancellation reason',
      })
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

  return {
    cancelOpen,
    deleteOpen,
    cancelReason,
    openCancel: () => setCancelOpen(true),
    openDelete: () => setDeleteOpen(true),
    setCancelOpen,
    setDeleteOpen,
    setCancelReason,
    handleCancel,
    handleDelete,
  }
}

interface ConfirmDialogProps {
  dialogs: TaskDetailDialogs
}

function CancelConfirmDialog({ dialogs }: ConfirmDialogProps) {
  return (
    <ConfirmDialog
      open={dialogs.cancelOpen}
      onOpenChange={(open) => {
        dialogs.setCancelOpen(open)
        if (!open) dialogs.setCancelReason('')
      }}
      title="Cancel Task"
      description="Are you sure? Please provide a reason for cancellation."
      confirmLabel="Cancel Task"
      variant="destructive"
      onConfirm={dialogs.handleCancel}
    >
      <textarea
        value={dialogs.cancelReason}
        onChange={(e) => dialogs.setCancelReason(e.target.value)}
        placeholder="Reason for cancellation..."
        className="mt-2 w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-foreground outline-none resize-y focus:ring-2 focus:ring-accent min-h-16"
        aria-label="Cancellation reason"
      />
    </ConfirmDialog>
  )
}

function DeleteConfirmDialog({ dialogs }: ConfirmDialogProps) {
  return (
    <ConfirmDialog
      open={dialogs.deleteOpen}
      onOpenChange={dialogs.setDeleteOpen}
      title="Delete Task"
      description="This action cannot be undone. The task and all associated data will be permanently deleted."
      confirmLabel="Delete"
      variant="destructive"
      onConfirm={dialogs.handleDelete}
    />
  )
}
