import { X } from 'lucide-react'
import { getRiskLevelLabel, getApprovalStatusLabel, type ApprovalPageFilters } from '@/utils/approvals'
import type { ApprovalRiskLevel, ApprovalStatus } from '@/api/types/enums'

const STATUSES = ['pending', 'approved', 'rejected', 'expired'] as const satisfies readonly ApprovalStatus[]
const RISK_LEVELS = ['critical', 'high', 'medium', 'low'] as const satisfies readonly ApprovalRiskLevel[]

export interface ApprovalFilterBarProps {
  filters: ApprovalPageFilters
  onFiltersChange: (filters: ApprovalPageFilters) => void
  pendingCount: number
  totalCount: number
  actionTypes: string[]
}

export function ApprovalFilterBar({
  filters,
  onFiltersChange,
  pendingCount,
  totalCount,
  actionTypes,
}: ApprovalFilterBarProps) {
  const hasActiveFilters = !!(filters.status || filters.riskLevel || filters.actionType || filters.search)

  function updateFilter<K extends keyof ApprovalPageFilters>(key: K, value: ApprovalPageFilters[K]) {
    onFiltersChange({ ...filters, [key]: value || undefined })
  }

  function clearFilters() {
    onFiltersChange({})
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {/* Status filter */}
        <select
          value={filters.status ?? ''}
          onChange={(e) => updateFilter('status', (e.target.value || undefined) as ApprovalStatus | undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{getApprovalStatusLabel(s)}</option>
          ))}
        </select>

        {/* Risk level filter */}
        <select
          value={filters.riskLevel ?? ''}
          onChange={(e) => updateFilter('riskLevel', (e.target.value || undefined) as ApprovalRiskLevel | undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by risk level"
        >
          <option value="">All risk levels</option>
          {RISK_LEVELS.map((r) => (
            <option key={r} value={r}>{getRiskLevelLabel(r)}</option>
          ))}
        </select>

        {/* Action type filter */}
        <select
          value={filters.actionType ?? ''}
          onChange={(e) => updateFilter('actionType', e.target.value || undefined)}
          className="h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Filter by action type"
        >
          <option value="">All action types</option>
          {actionTypes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        {/* Search */}
        <input
          type="text"
          value={filters.search ?? ''}
          onChange={(e) => updateFilter('search', e.target.value || undefined)}
          placeholder="Search approvals..."
          className="h-8 w-48 rounded-md border border-border bg-surface px-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Search approvals"
        />

        {/* Counts */}
        <span className="text-xs text-muted-foreground">
          {pendingCount} pending / {totalCount} total
        </span>
      </div>

      {/* Active filter pills */}
      {hasActiveFilters && (
        <div className="flex flex-wrap items-center gap-1.5">
          {filters.status && (
            <FilterPill label={`Status: ${getApprovalStatusLabel(filters.status)}`} onRemove={() => updateFilter('status', undefined)} />
          )}
          {filters.riskLevel && (
            <FilterPill label={`Risk: ${getRiskLevelLabel(filters.riskLevel)}`} onRemove={() => updateFilter('riskLevel', undefined)} />
          )}
          {filters.actionType && (
            <FilterPill label={`Type: ${filters.actionType}`} onRemove={() => updateFilter('actionType', undefined)} />
          )}
          {filters.search && (
            <FilterPill label={`Search: "${filters.search}"`} onRemove={() => updateFilter('search', undefined)} />
          )}
          <button
            type="button"
            onClick={clearFilters}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

interface FilterPillProps {
  label: string
  onRemove: () => void
}

function FilterPill({ label, onRemove }: FilterPillProps) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-[10px] text-text-secondary">
      {label}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 rounded-full p-0.5 hover:bg-border transition-colors"
        aria-label={`Remove filter: ${label}`}
      >
        <X className="size-2.5" />
      </button>
    </span>
  )
}
