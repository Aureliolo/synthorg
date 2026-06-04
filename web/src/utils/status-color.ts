/**
 * Shared TaskStatus to Tailwind background-color token mapping.
 *
 * Used by the mission-control Timeline scrubber and per-agent rows so
 * the same status colour is used everywhere a run's status is rendered.
 */

import type { TaskStatus } from '@/api/types/enums'

/**
 * Status -> Tailwind background token. Typed against the generated
 * ``TaskStatus`` union so a status added on the backend that lacks a
 * matching entry here surfaces as a compile-time miss instead of a
 * silent "always default colour" rendering bug. ``Partial<Record<...>>``
 * keeps the fallback below valid for the (rare) statuses that don't
 * have a meaningful colour (e.g. CREATED), and the function still
 * accepts plain ``string`` so callers that hold a raw WS frame status
 * don't need to coerce before calling.
 */
const STATUS_BG: Readonly<Partial<Record<TaskStatus, string>>> = {
  completed: 'bg-success',
  in_progress: 'bg-accent',
  in_review: 'bg-accent',
  blocked: 'bg-warning',
  interrupted: 'bg-warning',
  suspended: 'bg-warning',
  failed: 'bg-danger',
  cancelled: 'bg-danger',
  rejected: 'bg-danger',
}

/** Tailwind background-color token for a task / frame status. */
export function statusBgClass(status: string): string {
  return STATUS_BG[status as TaskStatus] ?? 'bg-text-secondary'
}
