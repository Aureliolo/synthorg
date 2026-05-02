import { create } from 'zustand'
import * as tasksApi from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import { useToastStore } from '@/stores/toast'
import {
  PRIORITY_VALUES,
  TASK_SOURCE_VALUES,
  TASK_STATUS_VALUES as TASK_STATUS_VALUES_TUPLE,
  TASK_TYPE_VALUES as TASK_TYPE_VALUES_TUPLE,
} from '@/api/types/enums'
import type {
  Complexity,
  CoordinationTopology,
  TaskStatus,
  TaskStructure,
} from '@/api/types/enums'
import type {
  CancelTaskRequest,
  CreateTaskRequest,
  Task,
  TaskFilters,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'

// Runtime-check sets derived from the canonical enum tuples in
// `@/api/types/enums`. Building them here (rather than re-declaring the
// literal list) keeps the validator in lockstep with the type union
// -- drift between the runtime check and the declared enum is caught
// at compile time.
// Status / priority / type / source are no longer pre-validated
// against the allowlist; sanitizeWsEnum owns that responsibility
// (see sanitizeTask). Rejecting unknown enum values here would drop
// the whole frame on rolling backend deploys.

// Enum sets for the remaining scalar/enum fields that ``sanitizeTask``
// previously copied through unchecked. Declared here so the validator
// and the TS union stay in lockstep via the ``as const satisfies``
// tuples these are derived from.
const COMPLEXITY_SET: ReadonlySet<string> = new Set<string>([
  'simple',
  'medium',
  'complex',
  'epic',
] satisfies readonly Complexity[])
const TASK_STRUCTURE_SET: ReadonlySet<string> = new Set<string>([
  'sequential',
  'parallel',
  'mixed',
] satisfies readonly TaskStructure[])
const COORDINATION_TOPOLOGY_SET: ReadonlySet<string> = new Set<string>([
  'sas',
  'centralized',
  'decentralized',
  'context_dependent',
  'auto',
] satisfies readonly CoordinationTopology[])
const log = createLogger('tasks')

interface TasksState {
  // Data
  tasks: Task[]
  selectedTask: Task | null
  total: number

  // Loading states
  loading: boolean
  loadingDetail: boolean
  error: string | null

  // Actions. Mutations follow the canonical store error contract: on
  // failure they log + emit an error toast + return a sentinel
  // (`null` for entity-returning ops, `false` for delete). Callers MUST
  // NOT wrap these in try/catch; check the sentinel and branch on it.
  fetchTasks: (filters?: TaskFilters) => Promise<void>
  fetchTask: (taskId: string) => Promise<void>
  createTask: (data: CreateTaskRequest) => Promise<Task | null>
  updateTask: (taskId: string, data: UpdateTaskRequest) => Promise<Task | null>
  transitionTask: (taskId: string, data: TransitionTaskRequest) => Promise<Task | null>
  cancelTask: (taskId: string, data: CancelTaskRequest) => Promise<Task | null>
  deleteTask: (taskId: string) => Promise<boolean>

  // Real-time
  handleWsEvent: (event: WsEvent) => void

  // Optimistic helpers
  pendingTransitions: Set<string>
  optimisticTransition: (taskId: string, targetStatus: TaskStatus) => () => void
  upsertTask: (task: Task) => void
  removeTask: (taskId: string) => void
}

const pendingTransitions = new Set<string>()

/**
 * Return a sanitized copy of a ``Task`` with every untrusted string
 * field routed through ``sanitizeWsString`` so control chars and
 * bidi overrides never reach the rendered UI. ``dependencies`` is a
 * string array; ``acceptance_criteria`` is an array of objects whose
 * ``description`` is the only freeform string field (``met`` is a
 * boolean validated by the shape guard already).
 */
function sanitizeTask(c: Task): Task {
  // Build the returned Task explicitly rather than spreading ``c``:
  // any future string field added to ``Task`` must be wired through
  // ``sanitizeWsString`` here, and a spread would silently bypass
  // sanitization for fields the author didn't remember to remap
  // (``created_at``, ``updated_at``, ``assigned_to``, ``project``,
  // nested ``artifacts_expected`` names, and so on).
  const sanitizeIds = (ids: readonly string[]) =>
    ids
      .map((id) => sanitizeWsString(id, 128) ?? '')
      .filter((id) => id.length > 0)
  // ``sanitizeNullable`` / ``sanitizeOptional`` preserve the null/
  // undefined signal when the raw value sanitizes to an empty string
  // -- a bidi-override-only payload for an optional timestamp should
  // come out as ``null`` (or ``undefined``), not an empty string the
  // UI would try to format.
  const sanitizeNullable = (value: string | null, cap: number): string | null => {
    if (value === null) return null
    const cleaned = sanitizeWsString(value, cap)
    return cleaned && cleaned.length > 0 ? cleaned : null
  }
  const sanitizeOptional = (
    value: string | undefined,
    cap: number,
  ): string | undefined => {
    if (value === undefined) return undefined
    const cleaned = sanitizeWsString(value, cap)
    return cleaned && cleaned.length > 0 ? cleaned : undefined
  }
  return {
    id: sanitizeWsString(c.id, 128) ?? '',
    title: sanitizeWsString(c.title, 256) ?? '',
    description: sanitizeWsString(c.description, 4096) ?? '',
    type: sanitizeWsEnum(c.type, TASK_TYPE_VALUES_TUPLE, 'admin', {
      maxLen: 64,
      field: 'task.type',
    }),
    status: sanitizeWsEnum(c.status, TASK_STATUS_VALUES_TUPLE, 'created', {
      maxLen: 64,
      field: 'task.status',
    }),
    priority: sanitizeWsEnum(c.priority, PRIORITY_VALUES, 'medium', {
      maxLen: 64,
      field: 'task.priority',
    }),
    project: sanitizeWsString(c.project, 128) ?? '',
    created_by: sanitizeWsString(c.created_by, 128) ?? '',
    assigned_to: sanitizeNullable(c.assigned_to, 128),
    reviewers: sanitizeIds(c.reviewers),
    dependencies: sanitizeIds(c.dependencies),
    artifacts_expected: c.artifacts_expected.map((a) => ({
      name: sanitizeWsString(a.name, 256) ?? '',
      type: sanitizeWsString(a.type, 64) ?? '',
    })),
    acceptance_criteria: c.acceptance_criteria.map((ac) => ({
      description: sanitizeWsString(ac.description, 512) ?? '',
      met: ac.met,
    })),
    estimated_complexity: c.estimated_complexity,
    budget_limit: c.budget_limit,
    cost: c.cost,
    deadline: sanitizeNullable(c.deadline, 64),
    max_retries: c.max_retries,
    parent_task_id: sanitizeNullable(c.parent_task_id, 128),
    delegation_chain: sanitizeIds(c.delegation_chain),
    task_structure: c.task_structure,
    coordination_topology: c.coordination_topology,
    source:
      c.source === undefined || c.source === null
        ? c.source
        : sanitizeWsEnum(c.source, TASK_SOURCE_VALUES, 'internal', {
            maxLen: 64,
            field: 'task.source',
          }),
    version: c.version,
    created_at: sanitizeOptional(c.created_at, 64),
    updated_at: sanitizeOptional(c.updated_at, 64),
  }
}

/** Each ``dependencies`` / ``reviewers`` / ``delegation_chain`` entry must be a plain string. */
function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((dep) => typeof dep === 'string')
}

/** Each ``artifacts_expected`` entry must have string ``name`` + ``type``. */
function isArtifactsExpectedShape(
  value: unknown,
): value is Array<{ name: string; type: string }> {
  if (!Array.isArray(value)) return false
  return value.every((entry) => {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return false
    const e = entry as { name?: unknown; type?: unknown }
    return typeof e.name === 'string' && typeof e.type === 'string'
  })
}

/**
 * Each ``acceptance_criteria`` entry must be a non-null object with a
 * string ``description`` AND a boolean ``met`` flag. Both fields are
 * part of the declared ``Task.acceptance_criteria`` shape; asserting
 * only ``description`` would let a malformed payload build a ``Task``
 * with ``criterion.met`` typed as something other than ``boolean``
 * and break every downstream consumer that branches on it.
 */
function isAcceptanceCriteriaShape(
  value: unknown,
): value is Array<{ description: string; met: boolean }> {
  if (!Array.isArray(value)) return false
  return value.every((ac) => {
    if (typeof ac !== 'object' || ac === null || Array.isArray(ac)) return false
    const entry = ac as { description?: unknown; met?: unknown }
    return typeof entry.description === 'string' && typeof entry.met === 'boolean'
  })
}

/** Nullable string -- used for optional identifiers / timestamps. */
function isNullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

/** Either ``undefined`` or a string -- used for the two optional timestamp fields. */
function isOptionalString(value: unknown): boolean {
  return value === undefined || typeof value === 'string'
}

/**
 * Element-wise string-array equality for detecting whether
 * ``sanitizeIds`` mutated any agent-id entry during sanitization.
 * A mutated entry means the wire value carried control/bidi chars
 * and we can't trust it to point at the intended agent.
 */
function arraysEqual(
  a: readonly string[],
  b: readonly string[],
): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

/**
 * Minimum structural check for a ``Task``-shaped WS payload. Validates
 * the required identifier + enum-typed fields (``status``, ``priority``,
 * ``type``, ``estimated_complexity``, ``coordination_topology`` -- each
 * checked against the canonical enum tuple so illegal values cannot be
 * smuggled in), the array fields (``reviewers``, ``dependencies``,
 * ``delegation_chain``, ``artifacts_expected``, ``acceptance_criteria``),
 * and the nullable / optional scalars that ``sanitizeTask`` reads.
 */
function isTaskShape(c: Record<string, unknown>): c is Record<string, unknown> & Task {
  // Enum fields routed through sanitizeWsEnum (status, priority, type,
  // source) accept any non-empty string here; the sanitizer applies
  // the allowlist + safe fallback. Rejecting unknown values would
  // drop the whole frame on rolling backend deploys.
  return (
    typeof c.id === 'string' &&
    typeof c.status === 'string' &&
    typeof c.title === 'string' &&
    typeof c.description === 'string' &&
    typeof c.priority === 'string' &&
    typeof c.type === 'string' &&
    typeof c.project === 'string' &&
    typeof c.created_by === 'string' &&
    (c.assigned_to === null || typeof c.assigned_to === 'string') &&
    isStringArray(c.reviewers) &&
    isStringArray(c.dependencies) &&
    isStringArray(c.delegation_chain) &&
    isArtifactsExpectedShape(c.artifacts_expected) &&
    isAcceptanceCriteriaShape(c.acceptance_criteria) &&
    // Nullable / optional fields consumed by ``sanitizeTask``. Without
    // these checks a payload like ``deadline: {}`` or ``source: 7``
    // would pass the guard and reach ``sanitizeWsString`` with a
    // non-string, breaking its length/bidi invariants.
    isNullableString(c.deadline) &&
    isNullableString(c.parent_task_id) &&
    isOptionalString(c.created_at) &&
    isOptionalString(c.updated_at) &&
    // ``version`` is ``number | undefined``; without this guard a
    // malformed payload could smuggle a non-numeric value through
    // ``sanitizeTask`` and break optimistic-concurrency downstream.
    (c.version === undefined || Number.isFinite(c.version)) &&
    // Numeric scalars: reject NaN/Infinity (``typeof === 'number'``
    // alone accepts both) so downstream budget math cannot be poisoned.
    Number.isFinite(c.budget_limit) &&
    (c.cost === undefined || Number.isFinite(c.cost)) &&
    Number.isFinite(c.max_retries) &&
    // Enum scalars: validate against the canonical tuples so a
    // malformed frame cannot inject an unsupported value.
    typeof c.estimated_complexity === 'string' &&
    COMPLEXITY_SET.has(c.estimated_complexity) &&
    (c.task_structure === null ||
      (typeof c.task_structure === 'string' &&
        TASK_STRUCTURE_SET.has(c.task_structure))) &&
    typeof c.coordination_topology === 'string' &&
    COORDINATION_TOPOLOGY_SET.has(c.coordination_topology) &&
    (c.source === undefined ||
      c.source === null ||
      typeof c.source === 'string')
  )
}

export const useTasksStore = create<TasksState>()((set, get) => ({
  tasks: [],
  selectedTask: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  pendingTransitions,

  fetchTasks: async (filters) => {
    set({ loading: true, error: null })
    try {
      const result = await tasksApi.listTasks(filters)
      set({
        tasks: result.data,
        total: result.data.length,
        loading: false,
      })
    } catch (err) {
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  fetchTask: async (taskId) => {
    set({ loadingDetail: true })
    try {
      const task = await tasksApi.getTask(taskId)
      set({ selectedTask: task, loadingDetail: false })
    } catch (err) {
      set({ loadingDetail: false, error: getErrorMessage(err) })
    }
  },

  createTask: async (data) => {
    try {
      const task = await tasksApi.createTask(data)
      set((s) => ({ tasks: [task, ...s.tasks], total: s.total + 1 }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Task ${task.title} created`,
      })
      return task
    } catch (err) {
      log.error('Create task failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to create task',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  updateTask: async (taskId, data) => {
    try {
      const task = await tasksApi.updateTask(taskId, data)
      get().upsertTask(task)
      useToastStore.getState().add({
        variant: 'success',
        title: `Task ${task.title} updated`,
      })
      return task
    } catch (err) {
      log.error('Update task failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to update task',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  transitionTask: async (taskId, data) => {
    try {
      const task = await tasksApi.transitionTask(taskId, data)
      get().upsertTask(task)
      useToastStore.getState().add({
        variant: 'success',
        title: `Task ${task.title} -> ${task.status}`,
      })
      return task
    } catch (err) {
      log.error('Transition task failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to transition task',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  cancelTask: async (taskId, data) => {
    try {
      const task = await tasksApi.cancelTask(taskId, data)
      get().upsertTask(task)
      useToastStore.getState().add({
        variant: 'success',
        title: `Task ${task.title} cancelled`,
      })
      return task
    } catch (err) {
      log.error('Cancel task failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to cancel task',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteTask: async (taskId) => {
    try {
      await tasksApi.deleteTask(taskId)
      get().removeTask(taskId)
      // Clear the dangling selection so a detail drawer doesn't
      // keep showing a task the store has already removed.
      if (get().selectedTask?.id === taskId) {
        set({ selectedTask: null })
      }
      useToastStore.getState().add({
        variant: 'success',
        title: 'Task deleted',
      })
      return true
    } catch (err) {
      log.error('Delete task failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to delete task',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  handleWsEvent: (event) => {
    const { payload } = event
    if (payload.task && typeof payload.task === 'object' && !Array.isArray(payload.task)) {
      const candidate = payload.task as Record<string, unknown>
      if (isTaskShape(candidate)) {
        // Sanitize identifier-bearing fields *before* the
        // pendingTransitions check so a frame whose id carries an
        // embedded bidi override or control character can't bypass
        // the optimistic-transition gate (which keys off the raw id)
        // and then sanitize down to the plain id to overwrite the
        // real task. We also reject when sanitization *mutates* any
        // identifier-bearing field -- ``assigned_to``, parent_task_id,
        // reviewers, dependencies, delegation_chain can all change
        // task-to-task / task-to-agent relationships silently if
        // control/bidi-carrying ids get normalized out.
        const sanitized = sanitizeTask(candidate)
        const requiredBlank =
          !sanitized.id || !sanitized.project || !sanitized.created_by
        const requiredMutated =
          sanitized.id !== candidate.id ||
          sanitized.project !== candidate.project ||
          sanitized.created_by !== candidate.created_by
        const assignedMutated = sanitized.assigned_to !== candidate.assigned_to
        const parentMutated = sanitized.parent_task_id !== candidate.parent_task_id
        const stringArraysMutated =
          !arraysEqual(sanitized.reviewers, candidate.reviewers) ||
          !arraysEqual(sanitized.dependencies, candidate.dependencies) ||
          !arraysEqual(sanitized.delegation_chain, candidate.delegation_chain)
        if (
          requiredBlank ||
          requiredMutated ||
          assignedMutated ||
          parentMutated ||
          stringArraysMutated
        ) {
          log.error(
            'Task payload lost or mutated identifier-bearing fields during sanitization, skipping upsert',
            sanitizeForLog({
              id: candidate.id,
              project: candidate.project,
              created_by: candidate.created_by,
              assigned_to: candidate.assigned_to,
              parent_task_id: candidate.parent_task_id,
            }),
          )
          return
        }
        if (pendingTransitions.has(sanitized.id)) return
        get().upsertTask(sanitized)
      } else {
        log.error('Received malformed task WS payload, skipping upsert', {
          id: sanitizeForLog(candidate.id),
          hasTitle: typeof candidate.title === 'string',
          hasStatus: typeof candidate.status === 'string',
        })
      }
    }
  },

  optimisticTransition: (taskId, targetStatus) => {
    const prev = get().tasks
    const taskIdx = prev.findIndex((t) => t.id === taskId)
    if (taskIdx === -1) return () => {}
    pendingTransitions.add(taskId)
    const oldTask = prev[taskIdx]!
    const updated = { ...oldTask, status: targetStatus }
    const newTasks = [...prev]
    newTasks[taskIdx] = updated
    set({ tasks: newTasks })
    return () => {
      pendingTransitions.delete(taskId)
      set({ tasks: prev })
    }
  },

  upsertTask: (task) => {
    pendingTransitions.delete(task.id)
    set((s) => {
      const idx = s.tasks.findIndex((t) => t.id === task.id)
      const newTasks = idx === -1 ? [task, ...s.tasks] : [...s.tasks]
      if (idx !== -1) newTasks[idx] = task
      const selectedTask = s.selectedTask?.id === task.id ? task : s.selectedTask
      return {
        tasks: newTasks,
        selectedTask,
        ...(idx === -1 ? { total: s.total + 1 } : {}),
      }
    })
  },

  removeTask: (taskId) => {
    set((s) => ({
      tasks: s.tasks.filter((t) => t.id !== taskId),
      total: Math.max(0, s.total - 1),
    }))
  },
}))
