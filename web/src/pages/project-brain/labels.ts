import type { BrainEntryKind, BrainEntryStatus } from '@/api/types'

/**
 * Human-readable labels for brain entry kinds and statuses.
 *
 * The wire enums are snake_case (`open_question`, `plan_revision`); rendering
 * them raw leaks storage shape into the UI. Two kind maps coexist on purpose:
 * the list groups entries under plural section headings ("Decisions") while a
 * single entry's pill reads as a singular noun ("Decision").
 */
export const BRAIN_KIND_LABEL: Record<BrainEntryKind, string> = {
  decision: 'Decision',
  open_question: 'Open question',
  blocker: 'Blocker',
  risk: 'Risk',
  dependency: 'Dependency',
  plan_revision: 'Plan revision',
}

export const BRAIN_KIND_HEADING: Record<BrainEntryKind, string> = {
  decision: 'Decisions',
  open_question: 'Open questions',
  blocker: 'Blockers',
  risk: 'Risks',
  dependency: 'Dependencies',
  plan_revision: 'Plan',
}

export const BRAIN_STATUS_LABEL: Record<BrainEntryStatus, string> = {
  open: 'Open',
  resolved: 'Resolved',
  accepted: 'Accepted',
  superseded: 'Superseded',
  blocked: 'Blocked',
  cleared: 'Cleared',
  active: 'Active',
  mitigated: 'Mitigated',
  retired: 'Retired',
}
