import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'

import { useRegisterShortcuts } from '@/hooks/use-shortcut-registry'
import { useTaskBoardData } from '@/hooks/useTaskBoardData'
import { useOptimisticUpdate } from '@/hooks/useOptimisticUpdate'
import { useToastStore } from '@/stores/toast'
import {
  KANBAN_COLUMNS,
  canTransitionTo,
  filterTasks,
  groupTasksByColumn,
  type TaskBoardFilters,
} from '@/utils/tasks'
import {
  PRIORITY_VALUES,
  TASK_STATUS_VALUES,
  TASK_TYPE_VALUES,
  type Priority,
  type TaskStatus,
  type TaskType,
} from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'

export interface TaskBoardController {
  data: ReturnType<typeof useTaskBoardData>
  filters: TaskBoardFilters
  filteredTasks: readonly Task[]
  columns: Record<string, Task[]>
  assignees: readonly string[]
  viewMode: 'board' | 'list'
  selectedTaskId: string | null
  createOpen: boolean
  showTerminal: boolean
  showDeps: boolean
  activeTask: Task | null
  sensors: ReturnType<typeof useSensors>
  setCreateOpen: (open: boolean) => void
  setShowTerminal: (open: boolean) => void
  setShowDeps: (open: boolean) => void
  handleFiltersChange: (filters: TaskBoardFilters) => void
  handleViewModeChange: (mode: 'board' | 'list') => void
  handleSelectTask: (taskId: string) => void
  handleClosePanel: () => void
  handleDragStart: (event: DragStartEvent) => void
  handleDragEnd: (event: DragEndEvent) => Promise<void>
}

export function useTaskBoardController(): TaskBoardController {
  const data = useTaskBoardData()
  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const [showTerminal, setShowTerminal] = useState(false)
  const [showDeps, setShowDeps] = useState(false)
  const [activeTask, setActiveTask] = useState<Task | null>(null)
  const { execute: executeOptimistic } = useOptimisticUpdate()

  const viewMode: 'board' | 'list' =
    searchParams.get('view') === 'list' ? 'list' : 'board'
  const selectedTaskId = searchParams.get('selected')

  useSyncSelectedTaskFromUrl(selectedTaskId, data.fetchTask)

  const derived = useTaskBoardDerivedState(searchParams, data.tasks)
  const urlHandlers = useTaskBoardUrlHandlers(setSearchParams, data.fetchTask)
  const dnd = useTaskBoardDnd(data, executeOptimistic, setActiveTask)

  useRegisterShortcuts([
    { keys: ['D'], label: 'Toggle dependencies overlay', group: 'Task board' },
    { keys: ['T'], label: 'Toggle terminal columns', group: 'Task board' },
    { keys: ['V'], label: 'Cycle view mode (board / list)', group: 'Task board' },
  ])
  useBoardKeyboardShortcuts({
    setShowDeps,
    setShowTerminal,
    onCycleView: () => urlHandlers.handleViewModeChange(viewMode === 'board' ? 'list' : 'board'),
  })

  return {
    data,
    filters: derived.filters,
    filteredTasks: derived.filteredTasks,
    columns: derived.columns,
    assignees: derived.assignees,
    viewMode,
    selectedTaskId,
    createOpen,
    showTerminal,
    showDeps,
    activeTask,
    sensors: dnd.sensors,
    setCreateOpen,
    setShowTerminal,
    setShowDeps,
    handleFiltersChange: urlHandlers.handleFiltersChange,
    handleViewModeChange: urlHandlers.handleViewModeChange,
    handleSelectTask: urlHandlers.handleSelectTask,
    handleClosePanel: urlHandlers.handleClosePanel,
    handleDragStart: dnd.handleDragStart,
    handleDragEnd: dnd.handleDragEnd,
  }
}

interface TaskBoardDerivedState {
  filters: TaskBoardFilters
  filteredTasks: readonly Task[]
  columns: Record<string, Task[]>
  assignees: readonly string[]
}

function useTaskBoardDerivedState(
  searchParams: URLSearchParams,
  tasks: readonly Task[],
): TaskBoardDerivedState {
  const filters = useMemo<TaskBoardFilters>(
    () => parseFiltersFromSearchParams(searchParams),
    [searchParams],
  )
  const filteredTasks = useMemo(() => filterTasks(tasks, filters), [tasks, filters])
  const columns = useMemo(() => groupTasksByColumn(filteredTasks), [filteredTasks])
  const assignees = useMemo(() => extractAssignees(tasks), [tasks])
  return { filters, filteredTasks, columns, assignees }
}

interface TaskBoardUrlHandlers {
  handleFiltersChange: (filters: TaskBoardFilters) => void
  handleViewModeChange: (mode: 'board' | 'list') => void
  handleSelectTask: (taskId: string) => void
  handleClosePanel: () => void
}

function useTaskBoardUrlHandlers(
  setSearchParams: ReturnType<typeof useSearchParams>[1],
  fetchTask: ReturnType<typeof useTaskBoardData>['fetchTask'],
): TaskBoardUrlHandlers {
  const handleFiltersChange = useCallback(
    (newFilters: TaskBoardFilters) =>
      setSearchParams((prev) => applyFilterParams(prev, newFilters)),
    [setSearchParams],
  )
  const handleViewModeChange = useCallback(
    (mode: 'board' | 'list') =>
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (mode === 'list') next.set('view', 'list')
        else next.delete('view')
        return next
      }),
    [setSearchParams],
  )
  const handleSelectTask = useCallback(
    (taskId: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('selected', taskId)
        return next
      })
      void fetchTask(taskId)
    },
    [setSearchParams, fetchTask],
  )
  const handleClosePanel = useCallback(
    () =>
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.delete('selected')
        return next
      }),
    [setSearchParams],
  )
  return { handleFiltersChange, handleViewModeChange, handleSelectTask, handleClosePanel }
}

interface TaskBoardDnd {
  sensors: ReturnType<typeof useSensors>
  handleDragStart: (event: DragStartEvent) => void
  handleDragEnd: (event: DragEndEvent) => Promise<void>
}

function useTaskBoardDnd(
  data: ReturnType<typeof useTaskBoardData>,
  executeOptimistic: ReturnType<typeof useOptimisticUpdate>['execute'],
  setActiveTask: (task: Task | null) => void,
): TaskBoardDnd {
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
      await processDragEnd(
        event,
        data.tasks,
        data.optimisticTransition,
        data.transitionTask,
        executeOptimistic,
      )
    },
    [data.tasks, data.optimisticTransition, data.transitionTask, executeOptimistic, setActiveTask],
  )
  return { sensors, handleDragStart, handleDragEnd }
}

function useSyncSelectedTaskFromUrl(
  selectedTaskId: string | null,
  fetchTask: ReturnType<typeof useTaskBoardData>['fetchTask'],
): void {
  const prevSelectedRef = useRef<string | null>(null)
  useEffect(() => {
    if (selectedTaskId && selectedTaskId !== prevSelectedRef.current) {
      void fetchTask(selectedTaskId)
    }
    prevSelectedRef.current = selectedTaskId
  }, [selectedTaskId, fetchTask])
}

interface BoardKeyboardShortcutsArgs {
  setShowDeps: (cb: (current: boolean) => boolean) => void
  setShowTerminal: (cb: (current: boolean) => boolean) => void
  onCycleView: () => void
}

function useBoardKeyboardShortcuts({
  setShowDeps,
  setShowTerminal,
  onCycleView,
}: BoardKeyboardShortcutsArgs): void {
  // ``onCycleView`` is an inline arrow in the caller (closes over viewMode
  // and urlHandlers), so its identity changes every render. Stashing it in a
  // ref keeps the effect's dep array stable while still invoking the latest
  // closure on each keypress.
  const onCycleViewRef = useRef(onCycleView)
  onCycleViewRef.current = onCycleView
  useEffect(() => {
    const handler = (event: KeyboardEvent) =>
      handleBoardShortcut(event, {
        setShowDeps,
        setShowTerminal,
        onCycleView: () => onCycleViewRef.current(),
      })
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setShowDeps, setShowTerminal])
}

function handleBoardShortcut(event: KeyboardEvent, args: BoardKeyboardShortcutsArgs): void {
  if (!isShortcutAllowed(event)) return
  const action = BOARD_SHORTCUT_DISPATCH[event.key.toUpperCase()]
  if (!action) return
  event.preventDefault()
  action(args)
}

function isShortcutAllowed(event: KeyboardEvent): boolean {
  if (event.metaKey || event.ctrlKey || event.altKey) return false
  if (event.repeat) return false
  return !isEditableTarget(event.target)
}

const BOARD_SHORTCUT_DISPATCH: Readonly<
  Record<string, (args: BoardKeyboardShortcutsArgs) => void>
> = {
  D: (args) => args.setShowDeps((current) => !current),
  T: (args) => args.setShowTerminal((current) => !current),
  V: (args) => args.onCycleView(),
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return target.isContentEditable
}

function parseFiltersFromSearchParams(searchParams: URLSearchParams): TaskBoardFilters {
  return {
    status: narrowToEnum<TaskStatus>(searchParams.get('status'), TASK_STATUS_VALUES),
    priority: narrowToEnum<Priority>(searchParams.get('priority'), PRIORITY_VALUES),
    assignee: searchParams.get('assignee') || undefined,
    taskType: narrowToEnum<TaskType>(searchParams.get('type'), TASK_TYPE_VALUES),
    search: searchParams.get('search') || undefined,
    dateFrom: searchParams.get('dateFrom') || undefined,
    dateTo: searchParams.get('dateTo') || undefined,
  }
}

/** Validate a URL-supplied string against an allowed enum tuple. Returns
 * ``undefined`` for missing or unknown values so callers can omit the
 * filter rather than apply a forged enum. URL filters are optional, so this
 * is the right shape; ``sanitizeWsEnum`` is for required WS payloads where
 * a fallback must always be supplied. */
function narrowToEnum<T extends string>(
  value: string | null,
  allowed: readonly T[],
): T | undefined {
  if (!value) return undefined
  return (allowed as readonly string[]).includes(value) ? (value as T) : undefined
}

const FILTER_PARAM_KEYS: readonly (keyof TaskBoardFilters)[] = [
  'status',
  'priority',
  'assignee',
  'taskType',
  'search',
  'dateFrom',
  'dateTo',
]

const FILTER_PARAM_NAMES: Readonly<Record<keyof TaskBoardFilters, string>> = {
  status: 'status',
  priority: 'priority',
  assignee: 'assignee',
  taskType: 'type',
  search: 'search',
  dateFrom: 'dateFrom',
  dateTo: 'dateTo',
}

function applyFilterParams(
  prev: URLSearchParams,
  newFilters: TaskBoardFilters,
): URLSearchParams {
  const next = new URLSearchParams(prev)
  const view = next.get('view')
  const selected = next.get('selected')
  for (const key of FILTER_PARAM_KEYS) next.delete(FILTER_PARAM_NAMES[key])
  for (const key of FILTER_PARAM_KEYS) {
    const value = newFilters[key]
    if (value) next.set(FILTER_PARAM_NAMES[key], value)
  }
  if (view) next.set('view', view)
  if (selected) next.set('selected', selected)
  return next
}

function extractAssignees(tasks: readonly Task[]): readonly string[] {
  const set = new Set<string>()
  for (const task of tasks) {
    if (task.assigned_to) set.add(task.assigned_to)
  }
  return Array.from(set).sort()
}

async function processDragEnd(
  event: DragEndEvent,
  tasks: readonly Task[],
  optimisticTransition: ReturnType<typeof useTaskBoardData>['optimisticTransition'],
  transitionTask: ReturnType<typeof useTaskBoardData>['transitionTask'],
  executeOptimistic: ReturnType<typeof useOptimisticUpdate>['execute'],
): Promise<void> {
  const { active, over } = event
  if (!over) return
  const taskId = active.id as string
  const targetColumnId = over.id as string
  const targetColumn = KANBAN_COLUMNS.find((col) => col.id === targetColumnId)
  if (!targetColumn) return
  const targetStatus = targetColumn.statuses[0]
  if (!targetStatus) return
  const sourceTask = tasks.find((t) => t.id === taskId)
  if (!sourceTask || sourceTask.status === targetStatus) return
  if (!canTransitionTo(sourceTask.status, targetStatus)) {
    useToastStore.getState().add({
      variant: 'warning',
      title: 'Invalid transition',
      description: `Cannot move from "${sourceTask.status}" to "${targetStatus}".`,
    })
    return
  }
  const result = await executeOptimistic(
    () => optimisticTransition(taskId, targetStatus),
    () =>
      transitionTask(taskId, {
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
  }
}
