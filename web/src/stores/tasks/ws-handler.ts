import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'
import type { DashboardTask } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'
import { pendingTransitions } from './_state'
import {
  isPlainObject,
  isStringArray,
  isTaskShape,
  sanitizeTask,
} from './sanitize'
import type { TasksGet } from './types'

const log = createLogger('tasks')

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
 * Equality for the nullable ``middleware_override`` chain after
 * sanitization. The chain selects which middleware runs for the task,
 * so a control/bidi-carrying entry silently normalized away would
 * redirect execution -- treat that as a mutation and reject the frame,
 * mirroring the ``reviewers`` / ``dependencies`` / ``delegation_chain``
 * gate. ``null`` and an absent field are equivalent ("no override").
 */
function nullableArraysEqual(
  sanitized: readonly string[] | null,
  original: unknown,
): boolean {
  if (sanitized === null) return original === null || original === undefined
  if (!isStringArray(original)) return false
  return arraysEqual(sanitized, original)
}

/**
 * Equality for an optional / nullable id field after sanitization.
 * Treats ``null`` and ``undefined`` as equivalent ("no value") and
 * only flags a real string-vs-string divergence.
 */
function nullableIdEqual(
  sanitized: string | null | undefined,
  original: unknown,
): boolean {
  return (sanitized ?? null) === (original ?? null)
}

type MutationKind = 'none' | 'critical' | 'collection'

interface SanitizationCheck {
  sanitized: DashboardTask
  mutation: MutationKind
}

function requiredFieldsBlankOrMutated(
  sanitized: DashboardTask,
  candidate: Record<string, unknown>,
): boolean {
  if (!sanitized.id || !sanitized.project || !sanitized.created_by) {
    return true
  }
  return sanitized.id !== candidate['id']
    || sanitized.project !== candidate['project']
    || sanitized.created_by !== candidate['created_by']
}

function stringArrayFieldsMutated(
  sanitized: DashboardTask,
  candidate: Record<string, unknown>,
): boolean {
  return !arraysEqual(
    sanitized.reviewers,
    candidate['reviewers'] as readonly string[],
  )
    || !arraysEqual(
      sanitized.dependencies,
      candidate['dependencies'] as readonly string[],
    )
    || !arraysEqual(
      sanitized.delegation_chain,
      candidate['delegation_chain'] as readonly string[],
    )
    || !nullableArraysEqual(
      sanitized.middleware_override,
      candidate['middleware_override'],
    )
}

function nullableIdFieldsMutated(
  sanitized: DashboardTask,
  candidate: Record<string, unknown>,
): boolean {
  return !nullableIdEqual(sanitized.assigned_to, candidate['assigned_to'])
    || !nullableIdEqual(sanitized.parent_task_id, candidate['parent_task_id'])
}

function checkSanitization(
  sanitized: DashboardTask,
  candidate: Record<string, unknown>,
): SanitizationCheck {
  // Distinguish "critical" mutations (id/project/created_by/assigned_to/
  // parent_task_id) from "collection" mutations (reviewers / dependencies
  // / delegation_chain / middleware_override). Both drop the frame, but
  // the latter additionally surfaces a user-visible toast so an
  // out-of-sync UI is not silent.
  if (
    requiredFieldsBlankOrMutated(sanitized, candidate)
    || nullableIdFieldsMutated(sanitized, candidate)
  ) {
    return { sanitized, mutation: 'critical' }
  }
  if (stringArrayFieldsMutated(sanitized, candidate)) {
    return { sanitized, mutation: 'collection' }
  }
  return { sanitized, mutation: 'none' }
}

function logMutationSkip(candidate: Record<string, unknown>): void {
  log.error(
    'Task payload lost or mutated identifier-bearing fields during sanitization, skipping upsert',
    sanitizeForLog({
      id: candidate['id'],
      project: candidate['project'],
      created_by: candidate['created_by'],
      assigned_to: candidate['assigned_to'],
      parent_task_id: candidate['parent_task_id'],
    }),
  )
}

function logMalformedSkip(candidate: Record<string, unknown>): void {
  log.error('Received malformed task WS payload, skipping upsert', {
    id: sanitizeForLog(candidate['id']),
    hasTitle: typeof candidate['title'] === 'string',
    hasStatus: typeof candidate['status'] === 'string',
  })
}

function notifyCollectionMutationSkip(taskId: string): void {
  useToastStore.getState().add({
    variant: 'warning',
    title: 'Task update dropped',
    description:
      'A live update for a task included unsafe characters in its collection'
      + ' fields and was discarded. Refresh the board to resync.',
  })
  log.warn(
    'Task collection-field sanitization mutated payload, skipping upsert',
    { id: sanitizeForLog(taskId) },
  )
}

export function createWsHandler(get: TasksGet) {
  return {
    handleWsEvent(event: WsEvent): void {
      const { payload } = event
      if (!isPlainObject(payload['task'])) return
      const candidate = payload['task']
      if (!isTaskShape(candidate)) {
        logMalformedSkip(candidate)
        return
      }
      // Sanitize identifier-bearing fields *before* the
      // pendingTransitions check so a frame whose id carries an
      // embedded bidi override or control character can't bypass
      // the optimistic-transition gate (which keys off the raw id)
      // and then sanitize down to the plain id to overwrite the
      // real task.
      const { sanitized, mutation } = checkSanitization(
        sanitizeTask(candidate),
        candidate,
      )
      if (mutation === 'critical') {
        logMutationSkip(candidate)
        return
      }
      if (mutation === 'collection') {
        notifyCollectionMutationSkip(sanitized.id)
        return
      }
      if (pendingTransitions.has(sanitized.id)) return
      get().upsertTask(sanitized)
    },
  }
}
