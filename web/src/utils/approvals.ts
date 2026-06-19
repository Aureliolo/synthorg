import type { ApprovalResponse } from '@/api/types/approvals'
import type { ApprovalRiskLevel, ApprovalStatus, UrgencyLevel } from '@/api/types/enums'
import type { SemanticColor } from '@/utils/agent-status'
import { AlertTriangle, Shield, ShieldAlert, ShieldCheck, type LucideIcon } from 'lucide-react'

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

// ── Client-side filtering ───────────────────────────────────

export interface ApprovalPageFilters {
  status?: ApprovalStatus | undefined
  riskLevel?: ApprovalRiskLevel | undefined
  actionType?: string | undefined
  search?: string | undefined
}

export function filterApprovals(
  approvals: readonly ApprovalResponse[],
  filters: ApprovalPageFilters,
): ApprovalResponse[] {
  let result = [...approvals]

  if (filters.status) {
    result = result.filter((a) => a.status === filters.status)
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
