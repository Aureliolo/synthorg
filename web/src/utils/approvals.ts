import type { ApprovalResponse } from '@/api/types/approvals'
import type {
  ApprovalRiskLevel,
  ApprovalSource,
  ApprovalStatus,
  RunOutcome,
  UrgencyLevel,
} from '@/api/types/enums'
import type { SemanticColor } from '@/utils/agent-status'
import { ROUTES } from '@/router/routes'
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Shield,
  ShieldAlert,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

/** Action type stamped on the review approval created for a failed run. */
const FAILED_RUN_ACTION_TYPE = 'review:task_failed'

/** Action type stamped on the review approval created for finished work. */
const COMPLETED_RUN_ACTION_TYPE = 'review:task_completion'

/**
 * Whether the approval asks for a verdict on a task's own work.
 *
 * The review gate parks other things too (the staffing reconciler's hire),
 * and those are not reviews of anything.
 */
function isTaskReview(actionType: string): boolean {
  return actionType === COMPLETED_RUN_ACTION_TYPE || actionType === FAILED_RUN_ACTION_TYPE
}

/** Deep-link to an approval, pre-selected in the queue. */
export function approvalDetailPath(approvalId: string): string {
  return `${ROUTES.APPROVALS}?selected=${encodeURIComponent(approvalId)}`
}

// ── Risk level color mapping ────────────────────────────────

const RISK_LEVEL_COLOR_MAP: Record<ApprovalRiskLevel, SemanticColor | 'accent-dim'> = {
  critical: 'danger',
  high: 'warning',
  medium: 'accent',
  low: 'accent-dim',
}

export function getRiskLevelColor(level: ApprovalRiskLevel): SemanticColor | 'accent-dim' {
  return RISK_LEVEL_COLOR_MAP[level]
}

// ── Risk level labels ───────────────────────────────────────

const RISK_LEVEL_LABELS: Record<ApprovalRiskLevel, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export function getRiskLevelLabel(level: ApprovalRiskLevel): string {
  return RISK_LEVEL_LABELS[level]
}

// ── Risk level icons ────────────────────────────────────────

const RISK_LEVEL_ICONS: Record<ApprovalRiskLevel, LucideIcon> = {
  critical: ShieldAlert,
  high: AlertTriangle,
  medium: Shield,
  low: ShieldCheck,
}

export function getRiskLevelIcon(level: ApprovalRiskLevel): LucideIcon {
  return RISK_LEVEL_ICONS[level]
}

// ── Run outcome (produced-output / failure) ─────────────────

const RUN_OUTCOME_COLOR_MAP: Record<RunOutcome, SemanticColor> = {
  succeeded: 'success',
  empty: 'warning',
  failed: 'danger',
}

export function getRunOutcomeColor(outcome: RunOutcome): SemanticColor {
  return RUN_OUTCOME_COLOR_MAP[outcome]
}

const RUN_OUTCOME_LABELS: Record<RunOutcome, string> = {
  succeeded: 'Produced output',
  empty: 'Produced nothing',
  failed: 'Run failed',
}

export function getRunOutcomeLabel(outcome: RunOutcome): string {
  return RUN_OUTCOME_LABELS[outcome]
}

const RUN_OUTCOME_ICONS: Record<RunOutcome, LucideIcon> = {
  succeeded: CheckCircle2,
  empty: CircleSlash,
  failed: XCircle,
}

export function getRunOutcomeIcon(outcome: RunOutcome): LucideIcon {
  return RUN_OUTCOME_ICONS[outcome]
}

/**
 * A failed run: either the resolved run outcome is `failed`, or the item
 * carries the failed-run action type (the fallback for a response built
 * without resolved run context, e.g. the lazy-expiry publish path). Failed
 * items get danger styling and are never shown as a routine low-risk approval.
 */
export function isFailedApproval(approval: ApprovalResponse): boolean {
  return approval.run?.outcome === 'failed' || approval.action_type === FAILED_RUN_ACTION_TYPE
}

// ── Approval step label (proposal-time vs review-gate) ──────

const APPROVAL_SOURCE_STEP_LABELS: Record<ApprovalSource, string> = {
  parked_context: 'Approve to continue',
  review_gate: 'Review completed work',
  conversational_intake: 'Approve to start',
  conversational_invite: 'Approve invite',
  plan_review: 'Review plan',
}

/**
 * Human label for which step of the propose then execute then review flow
 * this approval represents, so proposal-time and completion gates are never
 * confused. Failed/empty completions get their own truthful label.
 *
 * The failed case goes through `isFailedApproval` rather than reading the run
 * outcome again: the enrichment is optional and the action type is not, so
 * reading only the first put "Review completed work" over a task that failed,
 * beside that same card's Acknowledge/Retry buttons, which read the second.
 * One card, two answers, and the visible one was the wrong one.
 *
 * A `review_gate` approval that reviews no task borrows nothing from that
 * label either: the staffing reconciler parks a hire there, and a hire is a
 * decision to take, not work to look over.
 */
export function getApprovalStepLabel(approval: ApprovalResponse): string {
  if (isFailedApproval(approval)) return 'Review failed run'
  if (approval.run?.outcome === 'empty') return 'Review empty run'
  if (approval.source === 'review_gate' && !isTaskReview(approval.action_type)) {
    return APPROVAL_SOURCE_STEP_LABELS.parked_context
  }
  return APPROVAL_SOURCE_STEP_LABELS[approval.source]
}

// ── Approval status labels ──────────────────────────────────

const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
  expired: 'Expired',
}

export function getApprovalStatusLabel(status: ApprovalStatus): string {
  return APPROVAL_STATUS_LABELS[status]
}

// ── Approval status colors ──────────────────────────────────

const APPROVAL_STATUS_COLOR_MAP: Record<ApprovalStatus, SemanticColor | 'text-secondary'> = {
  pending: 'accent',
  approved: 'success',
  rejected: 'danger',
  expired: 'text-secondary',
}

export function getApprovalStatusColor(status: ApprovalStatus): SemanticColor | 'text-secondary' {
  return APPROVAL_STATUS_COLOR_MAP[status]
}

// ── Urgency formatting ──────────────────────────────────────

export function formatUrgency(secondsRemaining: number | null): string {
  if (secondsRemaining === null) return 'No expiry'
  if (secondsRemaining < 60) return '< 1m'

  const totalMinutes = Math.floor(secondsRemaining / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60

  if (hours === 0) return `${minutes}m`
  return `${hours}h ${minutes}m`
}

// ── Urgency color mapping ───────────────────────────────────

const URGENCY_COLOR_MAP: Record<UrgencyLevel, SemanticColor | 'text-secondary'> = {
  critical: 'danger',
  high: 'warning',
  normal: 'accent',
  no_expiry: 'text-secondary',
}

export function getUrgencyColor(level: UrgencyLevel): SemanticColor | 'text-secondary' {
  return URGENCY_COLOR_MAP[level]
}

// ── Risk level ordering ─────────────────────────────────────

export const RISK_LEVEL_ORDER: Record<ApprovalRiskLevel, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
}

const RISK_LEVELS_SORTED: ApprovalRiskLevel[] = ['critical', 'high', 'medium', 'low']

// ── Group by risk level ─────────────────────────────────────

export function groupByRiskLevel(
  approvals: readonly ApprovalResponse[],
): Map<ApprovalRiskLevel, ApprovalResponse[]> {
  const buckets: Record<ApprovalRiskLevel, ApprovalResponse[]> = {
    critical: [],
    high: [],
    medium: [],
    low: [],
  }

  for (const approval of approvals) {
    buckets[approval.risk_level].push(approval)
  }

  const result = new Map<ApprovalRiskLevel, ApprovalResponse[]>()
  for (const level of RISK_LEVELS_SORTED) {
    if (buckets[level].length > 0) {
      result.set(level, buckets[level])
    }
  }

  return result
}

// ── Shared CSS class mappings ───────────────────────────────

export const DOT_COLOR_CLASSES: Record<SemanticColor | 'accent-dim', string> = {
  danger: 'bg-danger',
  warning: 'bg-warning',
  accent: 'bg-accent',
  'accent-dim': 'bg-accent-dim',
  success: 'bg-success',
}

export const URGENCY_BADGE_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  danger: 'border-danger/30 bg-danger/10 text-danger',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  accent: 'border-accent/30 bg-accent/10 text-accent',
  success: 'border-success/30 bg-success/10 text-success',
  'text-secondary': 'border-border bg-surface text-text-secondary',
}

/** Bordered pill classes for a risk-level badge (includes the low-risk dim). */
export const RISK_BADGE_CLASSES: Record<SemanticColor | 'accent-dim', string> = {
  danger: 'border-danger/30 bg-danger/10 text-danger',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  accent: 'border-accent/30 bg-accent/10 text-accent',
  'accent-dim': 'border-accent-dim/30 bg-accent-dim/10 text-accent-dim',
  success: 'border-success/30 bg-success/10 text-success',
}

// ── Client-side filtering ───────────────────────────────────

/**
 * What the status control can be set to, including the archive.
 *
 * ``'all'`` is a value rather than the absence of one because the absence
 * means "the operator has not chosen", and that has to resolve to the queue
 * rather than to the archive. An approvals page is a place to decide things;
 * defaulting to every row ever decided showed 58 settled rows headed
 * "0 pending", under risk buckets all reading zero.
 */
export type ApprovalStatusFilter = ApprovalStatus | 'all'

/** What the status control shows when the operator has chosen nothing. */
export const DEFAULT_APPROVAL_STATUS: ApprovalStatusFilter = 'pending'

export interface ApprovalPageFilters {
  status?: ApprovalStatusFilter | undefined
  riskLevel?: ApprovalRiskLevel | undefined
  actionType?: string | undefined
  search?: string | undefined
}

/**
 * Whether *status* narrows the page away from the queue it opens on.
 *
 * @returns `true` when the operator has chosen something other than the
 *     default, so the choice is worth showing as a removable pill.
 */
export function isNarrowedStatus(
  status: ApprovalStatusFilter | undefined,
): boolean {
  return status != null && status !== DEFAULT_APPROVAL_STATUS
}

/**
 * Reference-shaped metadata keys, by the shape of the name rather than a list.
 * A key added by the backend next year is hidden until somebody writes down
 * why it is a word a person reads, which is the opposite default from an
 * allowlist. Mirrors the suffix rule `check_no_raw_id_in_ui.py` applies.
 */
const ID_SUFFIXES = ['_id', 'Id'] as const

/** References that ARE the word a person reads, so they stay visible. */
const ID_EXEMPT_SUFFIXES = ['model_id', 'modelId', 'correlation_id', 'correlationId'] as const

function isReferenceKey(key: string): boolean {
  if (ID_EXEMPT_SUFFIXES.some((suffix) => key.endsWith(suffix))) return false
  return ID_SUFFIXES.some((suffix) => key.endsWith(suffix))
}

/**
 * The approval metadata an operator surface may print.
 *
 * The map is backend-controlled and open-ended, so rendering it whole prints
 * whatever keys the producing feature happened to stamp: a hire approval
 * carries `request_id` and `candidate_id`, and both reached the drawer as raw
 * UUIDs under a "Metadata" heading. The ids still travel with the approval and
 * still drive the deep links; they are just not the thing a person is asked to
 * read.
 */
export function visibleMetadataEntries(
  metadata: Readonly<Record<string, unknown>>,
): [string, unknown][] {
  return Object.entries(metadata).filter(([key]) => !isReferenceKey(key))
}

export function filterApprovals(
  approvals: readonly ApprovalResponse[],
  filters: ApprovalPageFilters,
): ApprovalResponse[] {
  let result = [...approvals]

  // An absent status IS the pending queue, which is what the field's own
  // contract says and what every caller omitting it expects; reading absent as
  // "no filter" handed back settled approvals as though they still needed a
  // decision.
  const status = filters.status ?? DEFAULT_APPROVAL_STATUS
  if (status !== 'all') {
    result = result.filter((a) => a.status === status)
  }

  if (filters.riskLevel) {
    result = result.filter((a) => a.risk_level === filters.riskLevel)
  }

  if (filters.actionType) {
    result = result.filter((a) => a.action_type === filters.actionType)
  }

  if (filters.search) {
    const query = filters.search.toLowerCase()
    result = result.filter(
      (a) =>
        a.title.toLowerCase().includes(query) ||
        a.description.toLowerCase().includes(query),
    )
  }

  return result
}
