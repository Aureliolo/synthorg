/**
 * Shared TaskStatus to Tailwind background-color token mapping.
 *
 * Used by the mission-control Timeline scrubber and per-agent rows so
 * the same status colour is used everywhere a run's status is rendered.
 */

const STATUS_BG: Record<string, string> = {
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
  return STATUS_BG[status] ?? 'bg-text-secondary'
}
