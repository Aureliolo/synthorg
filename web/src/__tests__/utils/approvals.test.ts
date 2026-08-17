import { describe, expect, it } from 'vitest'
import {
  RISK_LEVEL_ORDER,
  filterApprovals,
  formatUrgency,
  getApprovalStatusColor,
  getApprovalStatusLabel,
  getApprovalStepLabel,
  getRiskLevelColor,
  getRiskLevelIcon,
  getRiskLevelLabel,
  getRunOutcomeColor,
  getRunOutcomeLabel,
  getUrgencyColor,
  groupByRiskLevel,
  isFailedApproval,
} from '@/utils/approvals'
import type { ApprovalRiskLevel, ApprovalStatus, UrgencyLevel } from '@/api/types/enums'
import { makeApproval } from '@/__tests__/helpers/factories'
import { ShieldAlert, AlertTriangle, Shield, ShieldCheck } from 'lucide-react'

// ── Risk level color mapping ──────────────────────────────────

describe('getRiskLevelColor', () => {
  it.each<[ApprovalRiskLevel, string]>([
    ['critical', 'danger'],
    ['high', 'warning'],
    ['medium', 'accent'],
    ['low', 'accent-dim'],
  ])('maps %s to %s', (level, expected) => {
    expect(getRiskLevelColor(level)).toBe(expected)
  })
})

// ── Risk level labels ─────────────────────────────────────────

describe('getRiskLevelLabel', () => {
  it.each<[ApprovalRiskLevel, string]>([
    ['critical', 'Critical'],
    ['high', 'High'],
    ['medium', 'Medium'],
    ['low', 'Low'],
  ])('maps %s to %s', (level, expected) => {
    expect(getRiskLevelLabel(level)).toBe(expected)
  })
})

// ── Risk level icons ──────────────────────────────────────────

describe('getRiskLevelIcon', () => {
  it('returns the expected icon for each risk level', () => {
    const expected: Record<ApprovalRiskLevel, unknown> = {
      critical: ShieldAlert,
      high: AlertTriangle,
      medium: Shield,
      low: ShieldCheck,
    }
    for (const [level, icon] of Object.entries(expected)) {
      expect(getRiskLevelIcon(level as ApprovalRiskLevel)).toBe(icon)
    }
  })

  it('returns distinct icons for each risk level', () => {
    const icons = new Set(['critical', 'high', 'medium', 'low'].map((l) => getRiskLevelIcon(l as ApprovalRiskLevel)))
    expect(icons.size).toBe(4)
  })
})

// ── Approval status labels ────────────────────────────────────

describe('getApprovalStatusLabel', () => {
  it.each<[ApprovalStatus, string]>([
    ['pending', 'Pending'],
    ['approved', 'Approved'],
    ['rejected', 'Rejected'],
    ['expired', 'Expired'],
  ])('maps %s to %s', (status, expected) => {
    expect(getApprovalStatusLabel(status)).toBe(expected)
  })
})

// ── Approval status colors ────────────────────────────────────

describe('getApprovalStatusColor', () => {
  it.each<[ApprovalStatus, string]>([
    ['pending', 'accent'],
    ['approved', 'success'],
    ['rejected', 'danger'],
    ['expired', 'text-secondary'],
  ])('maps %s to %s', (status, expected) => {
    expect(getApprovalStatusColor(status)).toBe(expected)
  })
})

// ── Urgency formatting ────────────────────────────────────────

describe('formatUrgency', () => {
  it('returns "No expiry" for null', () => {
    expect(formatUrgency(null)).toBe('No expiry')
  })

  it('returns "< 1m" for 0 seconds', () => {
    expect(formatUrgency(0)).toBe('< 1m')
  })

  it('returns "< 1m" for 59 seconds', () => {
    expect(formatUrgency(59)).toBe('< 1m')
  })

  it('returns minutes only for < 1 hour', () => {
    expect(formatUrgency(60)).toBe('1m')
    expect(formatUrgency(300)).toBe('5m')
    expect(formatUrgency(3540)).toBe('59m')
  })

  it('returns hours and minutes for >= 1 hour', () => {
    expect(formatUrgency(3600)).toBe('1h 0m')
    expect(formatUrgency(3660)).toBe('1h 1m')
    expect(formatUrgency(7200)).toBe('2h 0m')
    expect(formatUrgency(8100)).toBe('2h 15m')
  })

  it('returns "< 1m" for negative values', () => {
    expect(formatUrgency(-10)).toBe('< 1m')
  })
})

// ── Urgency color mapping ─────────────────────────────────────

describe('getUrgencyColor', () => {
  it.each<[UrgencyLevel, string]>([
    ['critical', 'danger'],
    ['high', 'warning'],
    ['normal', 'accent'],
    ['no_expiry', 'text-secondary'],
  ])('maps %s to %s', (level, expected) => {
    expect(getUrgencyColor(level)).toBe(expected)
  })
})

// ── Risk level ordering ───────────────────────────────────────

describe('RISK_LEVEL_ORDER', () => {
  it('orders critical < high < medium < low', () => {
    expect(RISK_LEVEL_ORDER.critical).toBeLessThan(RISK_LEVEL_ORDER.high)
    expect(RISK_LEVEL_ORDER.high).toBeLessThan(RISK_LEVEL_ORDER.medium)
    expect(RISK_LEVEL_ORDER.medium).toBeLessThan(RISK_LEVEL_ORDER.low)
  })
})

// ── Group by risk level ───────────────────────────────────────

describe('groupByRiskLevel', () => {
  it('groups approvals by risk level in order', () => {
    const approvals = [
      makeApproval('1', { risk_level: 'low' }),
      makeApproval('2', { risk_level: 'critical' }),
      makeApproval('3', { risk_level: 'medium' }),
      makeApproval('4', { risk_level: 'critical' }),
      makeApproval('5', { risk_level: 'high' }),
    ]

    const groups = groupByRiskLevel(approvals)
    const keys = [...groups.keys()]

    expect(keys).toEqual(['critical', 'high', 'medium', 'low'])
    expect(groups.get('critical')!.map((a) => a.id)).toEqual(['2', '4'])
    expect(groups.get('high')!.map((a) => a.id)).toEqual(['5'])
    expect(groups.get('medium')!.map((a) => a.id)).toEqual(['3'])
    expect(groups.get('low')!.map((a) => a.id)).toEqual(['1'])
  })

  it('returns empty map for no approvals', () => {
    const groups = groupByRiskLevel([])
    expect(groups.size).toBe(0)
  })

  it('omits risk levels with no approvals', () => {
    const approvals = [makeApproval('1', { risk_level: 'high' })]
    const groups = groupByRiskLevel(approvals)
    expect(groups.size).toBe(1)
    expect(groups.has('high')).toBe(true)
    expect(groups.has('critical')).toBe(false)
  })
})

// ── Run outcome + failure + step label ────────────────────────

describe('getRunOutcomeColor', () => {
  it('maps succeeded/empty/failed to success/warning/danger', () => {
    expect(getRunOutcomeColor('succeeded')).toBe('success')
    expect(getRunOutcomeColor('empty')).toBe('warning')
    expect(getRunOutcomeColor('failed')).toBe('danger')
  })
})

describe('getRunOutcomeLabel', () => {
  it('gives human labels', () => {
    expect(getRunOutcomeLabel('succeeded')).toBe('Produced output')
    expect(getRunOutcomeLabel('empty')).toBe('Produced nothing')
    expect(getRunOutcomeLabel('failed')).toBe('Run failed')
  })
})

describe('isFailedApproval', () => {
  it('is true when the run outcome is failed', () => {
    expect(isFailedApproval(makeApproval('1', {
      run: { outcome: 'failed', produced_artifact_count: 0, artifacts: [] },
    }))).toBe(true)
  })

  it('is true for the failed-run action type even without a resolved run', () => {
    expect(isFailedApproval(makeApproval('2', { action_type: 'review:task_failed' }))).toBe(true)
  })

  it('is false for a succeeded run', () => {
    expect(isFailedApproval(makeApproval('3', {
      run: { outcome: 'succeeded', produced_artifact_count: 1, artifacts: [] },
    }))).toBe(false)
  })
})

describe('getApprovalStepLabel', () => {
  it('labels proposal-time vs review-gate distinctly', () => {
    expect(getApprovalStepLabel(makeApproval('1', { source: 'conversational_intake' }))).toBe(
      'Approve to start',
    )
    expect(getApprovalStepLabel(makeApproval('2', { source: 'review_gate' }))).toBe(
      'Review completed work',
    )
  })

  it('labels failed and empty runs truthfully regardless of source', () => {
    expect(getApprovalStepLabel(makeApproval('3', {
      source: 'review_gate',
      run: { outcome: 'failed', produced_artifact_count: 0, artifacts: [] },
    }))).toBe('Review failed run')
    expect(getApprovalStepLabel(makeApproval('4', {
      source: 'review_gate',
      run: { outcome: 'empty', produced_artifact_count: 0, artifacts: [] },
    }))).toBe('Review empty run')
  })
})

// ── Client-side filtering ─────────────────────────────────────

describe('filterApprovals', () => {
  const approvals = [
    makeApproval('1', { status: 'pending', risk_level: 'critical', action_type: 'deploy:production', title: 'Deploy API' }),
    makeApproval('2', { status: 'approved', risk_level: 'high', action_type: 'code:create', title: 'Create service' }),
    makeApproval('3', { status: 'pending', risk_level: 'medium', action_type: 'code:create', title: 'Refactor utils' }),
    makeApproval('4', { status: 'rejected', risk_level: 'low', action_type: 'docs:write', title: 'Update readme' }),
  ]

  it('reads an absent status as the pending queue', () => {
    // Its own contract: absent means the default, not "every status". The
    // previous reading handed settled approvals back as still-undecided.
    expect(filterApprovals(approvals, {}).map((a) => a.id)).toEqual(['1', '3'])
  })

  it('returns every status only when asked explicitly', () => {
    expect(filterApprovals(approvals, { status: 'all' })).toHaveLength(4)
  })

  it('filters by status', () => {
    const result = filterApprovals(approvals, { status: 'pending' })
    expect(result.map((a) => a.id)).toEqual(['1', '3'])
  })

  it('filters by risk level', () => {
    const result = filterApprovals(approvals, { riskLevel: 'critical' })
    expect(result.map((a) => a.id)).toEqual(['1'])
  })

  it('filters by action type within the default queue', () => {
    // The pending default still applies: '2' is approved, so it belongs to
    // the archive rather than to the queue this filter narrows.
    const result = filterApprovals(approvals, { actionType: 'code:create' })
    expect(result.map((a) => a.id)).toEqual(['3'])
  })

  it('filters by action type across every status when asked', () => {
    const result = filterApprovals(approvals, {
      actionType: 'code:create',
      status: 'all',
    })
    expect(result.map((a) => a.id)).toEqual(['2', '3'])
  })

  it('filters by search (title)', () => {
    const result = filterApprovals(approvals, { search: 'deploy' })
    expect(result.map((a) => a.id)).toEqual(['1'])
  })

  it('filters by search (description)', () => {
    const items = [makeApproval('10', { description: 'Critical auth fix' })]
    const result = filterApprovals(items, { search: 'auth' })
    expect(result).toHaveLength(1)
  })

  it('search is case-insensitive', () => {
    const result = filterApprovals(approvals, { search: 'DEPLOY' })
    expect(result.map((a) => a.id)).toEqual(['1'])
  })

  it('search matches title when description is empty', () => {
    const items = [makeApproval('10', { title: 'Deploy API', description: '' })]
    const result = filterApprovals(items, { search: 'deploy' })
    expect(result).toHaveLength(1)
  })

  it('combines multiple filters with AND', () => {
    const result = filterApprovals(approvals, { status: 'pending', riskLevel: 'medium' })
    expect(result.map((a) => a.id)).toEqual(['3'])
  })
})
