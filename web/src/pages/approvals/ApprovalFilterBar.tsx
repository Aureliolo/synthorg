import { X } from 'lucide-react'
import { getRiskLevelLabel, getApprovalStatusLabel, type ApprovalPageFilters } from '@/utils/approvals'
import type { ApprovalRiskLevel, ApprovalStatus } from '@/api/types/enums'

const STATUSES = ['pending', 'approved', 'rejected', 'expired'] as const satisfies readonly ApprovalStatus[]
const RISK_LEVELS = ['critical', 'high', 'medium', 'low'] as const satisfies readonly ApprovalRiskLevel[]

/** Narrow a raw select value to ``ApprovalStatus`` by membership, else ``undefined``. */
function parseStatus(value: string): ApprovalStatus | undefined {
  return (STATUSES as readonly string[]).includes(value) ? (value as ApprovalStatus) : undefined
}

/** Narrow a raw select value to ``ApprovalRiskLevel`` by membership, else ``undefined``. */
function parseRiskLevel(value: string): ApprovalRiskLevel | undefined {
  return (RISK_LEVELS as readonly string[]).includes(value) ? (value as ApprovalRiskLevel) : undefined
}

const SELECT_CLASS =
  'h-8 rounded-md border border-border bg-surface px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent'

type UpdateFilterFn = <K extends keyof ApprovalPageFilters>(key: K, value: ApprovalPageFilters[K]) => void

export interface ApprovalFilterBarProps {
  filters: ApprovalPageFilters
  onFiltersChange: (filters: ApprovalPageFilters) => void
  pendingCount: number
  totalCount: number
  actionTypes: string[]
}

function hasAnyFilter(filters: ApprovalPageFilters): boolean {
  return Boolean(filters.status || filters.riskLevel || filters.actionType || filters.search)
}

interface ApprovalFilterControlsProps {
  filters: ApprovalPageFilters
  actionTypes: string[]
  pendingCount: number
  totalCount: number
  onUpdate: UpdateFilterFn
}

function ApprovalFilterControls({
  filters,
  actionTypes,
  pendingCount,
  totalCount,
  onUpdate,
}: ApprovalFilterControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={filters.status ?? ''}
        onChange={(e) => onUpdate('status', parseStatus(e.target.value))}
        className={SELECT_CLASS}
        aria-label="Filter by status"
      >
        <option value="">All statuses</option>
        {STATUSES.map((s) => (
          <option key={s} value={s}>
            {getApprovalStatusLabel(s)}
          </option>
        ))}
      </select>

      <select
        value={filters.riskLevel ?? ''}
        onChange={(e) => onUpdate('riskLevel', parseRiskLevel(e.target.value))}
        className={SELECT_CLASS}
        aria-label="Filter by risk level"
      >
        <option value="">All risk levels</option>
        {RISK_LEVELS.map((r) => (
          <option key={r} value={r}>
            {getRiskLevelLabel(r)}
          </option>
        ))}
      </select>

      <select
        value={filters.actionType ?? ''}
        onChange={(e) => onUpdate('actionType', e.target.value || undefined)}
        className={SELECT_CLASS}
        aria-label="Filter by action type"
      >
        <option value="">All action types</option>
        {actionTypes.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <input
        type="text"
        value={filters.search ?? ''}
        onChange={(e) => onUpdate('search', e.target.value || undefined)}
        placeholder="Search approvals..."
        className="h-8 w-48 rounded-md border border-border bg-surface px-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent"
        aria-label="Search approvals"
      />

      <span className="text-xs text-muted-foreground">
        {pendingCount} pending / {totalCount} total
      </span>
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

function ActiveFilterPills({
  filters,
  onUpdate,
  onClear,
}: {
  filters: ApprovalPageFilters
  onUpdate: UpdateFilterFn
  onClear: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {filters.status != null && (
        <FilterPill
          label={`Status: ${getApprovalStatusLabel(filters.status)}`}
          onRemove={() => onUpdate('status', undefined)}
        />
      )}
      {filters.riskLevel != null && (
        <FilterPill
          label={`Risk: ${getRiskLevelLabel(filters.riskLevel)}`}
          onRemove={() => onUpdate('riskLevel', undefined)}
        />
      )}
      {filters.actionType != null && (
        <FilterPill label={`Type: ${filters.actionType}`} onRemove={() => onUpdate('actionType', undefined)} />
      )}
      {filters.search != null && (
        <FilterPill label={`Search: "${filters.search}"`} onRemove={() => onUpdate('search', undefined)} />
      )}
      <button
        type="button"
        onClick={onClear}
        className="text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        Clear all
      </button>
    </div>
  )
}

export function ApprovalFilterBar({
  filters,
  onFiltersChange,
  pendingCount,
  totalCount,
  actionTypes,
}: ApprovalFilterBarProps) {
  const updateFilter: UpdateFilterFn = (key, value) => {
    onFiltersChange({ ...filters, [key]: value || undefined })
  }

  return (
    <div className="space-y-2">
      <ApprovalFilterControls
        filters={filters}
        actionTypes={actionTypes}
        pendingCount={pendingCount}
        totalCount={totalCount}
        onUpdate={updateFilter}
      />
      {hasAnyFilter(filters) && (
        <ActiveFilterPills filters={filters} onUpdate={updateFilter} onClear={() => onFiltersChange({})} />
      )}
    </div>
  )
}
