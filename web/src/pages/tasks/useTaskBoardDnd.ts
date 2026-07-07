import { useCallback } from 'react'
import {
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'

import { useTaskBoardData } from '@/hooks/useTaskBoardData'
import { useOptimisticUpdate } from '@/hooks/useOptimisticUpdate'
import { useToastStore } from '@/stores/toast'
import { KANBAN_COLUMNS, canTransitionTo, type KanbanColumn, type KanbanColumnId } from '@/utils/tasks'
import type { BoardPolicy } from '@/hooks/useBoardPolicy'
import type { Task } from '@/api/types/tasks'
import type { TaskStatus } from '@/api/types/enums'

export interface TaskBoardDnd {
  sensors: ReturnType<typeof useSensors>
  handleDragStart: (event: DragStartEvent) => void
  handleDragEnd: (event: DragEndEvent) => Promise<void>
}

interface DragDeps {
  tasks: readonly Task[]
  optimisticTransition: ReturnType<typeof useTaskBoardData>['optimisticTransition']
  transitionTask: ReturnType<typeof useTaskBoardData>['transitionTask']
  executeOptimistic: ReturnType<typeof useOptimisticUpdate>['execute']
  boardPolicy: BoardPolicy | null
  refreshBoard: () => void
}

/**
 * Drag-and-drop wiring for the Kanban board: sensors, the active-card
 * lifecycle, and the drop handler that transitions a task and (when the
 * org enforces WIP limits) rejects a drop into a full flow column.
 */
export function useTaskBoardDnd(
  data: ReturnType<typeof useTaskBoardData>,
  setActiveTask: (task: Task | null) => void,
  boardPolicy: BoardPolicy | null,
  refreshBoard: () => void,
): TaskBoardDnd {
  const { execute: executeOptimistic } = useOptimisticUpdate()
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor),
  )
  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const task = (event.active.data.current as { task?: Task } | undefined)?.task
      if (task) setActiveTask(task)
    },
    [setActiveTask],
  )
  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveTask(null)
      await processDragEnd(event, {
        tasks: data.tasks,
        optimisticTransition: data.optimisticTransition,
        transitionTask: data.transitionTask,
        executeOptimistic,
        boardPolicy,
        refreshBoard,
      })
    },
    [
      data.tasks,
      data.optimisticTransition,
      data.transitionTask,
      executeOptimistic,
      setActiveTask,
      boardPolicy,
      refreshBoard,
    ],
  )
  return { sensors, handleDragStart, handleDragEnd }
}

interface DragMove {
  taskId: string
  targetColumn: KanbanColumn
  targetStatus: TaskStatus
  sourceTask: Task
}

/** Resolve a drop event into a concrete task move, or ``null`` for a no-op. */
function resolveDragMove(event: DragEndEvent, tasks: readonly Task[]): DragMove | null {
  const { active, over } = event
  if (!over) return null
  const taskId = active.id as string
  const targetColumn = KANBAN_COLUMNS.find((col) => col.id === (over.id as string))
  const targetStatus = targetColumn?.statuses[0]
  if (!targetColumn || !targetStatus) return null
  const sourceTask = tasks.find((t) => t.id === taskId)
  if (!sourceTask || sourceTask.status === targetStatus) return null
  return { taskId, targetColumn, targetStatus, sourceTask }
}

async function processDragEnd(event: DragEndEvent, deps: DragDeps): Promise<void> {
  const move = resolveDragMove(event, deps.tasks)
  if (!move) return
  const { taskId, targetColumn, targetStatus, sourceTask } = move
  if (!canTransitionTo(sourceTask.status, targetStatus)) {
    _warn('Invalid transition', `Cannot move from "${sourceTask.status}" to "${targetStatus}".`)
    return
  }
  if (isWipBlocked(deps.boardPolicy, targetColumn.id, sourceTask.status)) {
    _warn(
      'WIP limit reached',
      `"${targetColumn.label}" is at its work-in-progress limit. `
        + 'Finish or move a task out of that column first.',
    )
    return
  }
  const result = await deps.executeOptimistic(
    () => deps.optimisticTransition(taskId, targetStatus),
    () =>
      deps.transitionTask(taskId, {
        target_status: targetStatus,
        expected_version: sourceTask.version ?? null,
      }),
  )
  if (result === null) {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Could not move task',
      description: 'It may have changed status. Refresh and try again.',
    })
    return
  }
  // Occupancy changed: re-fetch so the WIP badges reflect the move.
  deps.refreshBoard()
}

/**
 * Whether a drop into ``targetColumnId`` must be blocked because the org
 * enforces WIP limits and the destination flow column is already at its
 * limit. A move originating from the same column is never blocked (it does
 * not raise the destination's occupancy).
 */
function isWipBlocked(
  boardPolicy: BoardPolicy | null,
  targetColumnId: KanbanColumnId,
  sourceStatus: TaskStatus,
): boolean {
  if (!boardPolicy?.enforceWip) return false
  const wip = boardPolicy.wipByColumn[targetColumnId]
  if (!wip) return false
  const targetColumn = KANBAN_COLUMNS.find((col) => col.id === targetColumnId)
  const alreadyInColumn = targetColumn?.statuses.includes(sourceStatus) ?? false
  return !alreadyInColumn && wip.count >= wip.limit
}

function _warn(title: string, description: string): void {
  useToastStore.getState().add({ variant: 'warning', title, description })
}
