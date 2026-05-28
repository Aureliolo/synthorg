import { useCallback } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getMessageTypeLabel } from '@/utils/messages'
import type { MessagePageFilters } from '@/utils/messages'
import type { MessagePriority, MessageType } from '@/api/types/messages'

const MESSAGE_TYPES: MessageType[] = [
  'task_update',
  'question',
  'announcement',
  'review_request',
  'approval',
  'delegation',
  'status_report',
  'escalation',
  'meeting_contribution',
  'hr_notification',
]

const PRIORITIES: MessagePriority[] = ['low', 'normal', 'high', 'urgent']

const SELECT_CLASSES = cn(
  'h-7 rounded-md border border-border bg-surface px-2 text-xs text-foreground',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
)

const SEARCH_INPUT_CLASSES = cn(
  'h-7 w-full rounded-md border border-border bg-surface pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
)

interface MessageFilterBarProps {
  filters: MessagePageFilters
  onFiltersChange: (filters: MessagePageFilters) => void
  totalCount: number
  filteredCount?: number
}

export function MessageFilterBar({
  filters,
  onFiltersChange,
  totalCount,
  filteredCount,
}: MessageFilterBarProps) {
  const handleTypeChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value as MessageType | ''
      onFiltersChange({ ...filters, type: value || undefined })
    },
    [filters, onFiltersChange],
  )
  const handlePriorityChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value as MessagePriority | ''
      onFiltersChange({ ...filters, priority: value || undefined })
    },
    [filters, onFiltersChange],
  )
  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      // Don't trim mid-typing: the user can't type "hello world" if every
      // keystroke strips trailing whitespace. The downstream filter logic
      // handles whitespace tolerance; if a fully-trimmed value is required
      // for persistence, do that at submit/blur, not onChange.
      const value = e.target.value
      onFiltersChange({ ...filters, search: value || undefined })
    },
    [filters, onFiltersChange],
  )

  const hasFilters = Boolean(filters.type || filters.priority || filters.search)

  return (
    <div className="space-y-2">
      <FilterControlsRow
        filters={filters}
        totalCount={totalCount}
        filteredCount={filteredCount}
        hasFilters={hasFilters}
        onTypeChange={handleTypeChange}
        onPriorityChange={handlePriorityChange}
        onSearchChange={handleSearchChange}
      />
      {hasFilters && <ActiveFilterPills filters={filters} onFiltersChange={onFiltersChange} />}
    </div>
  )
}

interface FilterControlsRowProps {
  filters: MessagePageFilters
  totalCount: number
  filteredCount: number | undefined
  hasFilters: boolean
  onTypeChange: (e: React.ChangeEvent<HTMLSelectElement>) => void
  onPriorityChange: (e: React.ChangeEvent<HTMLSelectElement>) => void
  onSearchChange: (e: React.ChangeEvent<HTMLInputElement>) => void
}

function FilterControlsRow({
  filters,
  totalCount,
  filteredCount,
  hasFilters,
  onTypeChange,
  onPriorityChange,
  onSearchChange,
}: FilterControlsRowProps) {
  const showFilteredCount = hasFilters && filteredCount !== undefined
  const countLabel = showFilteredCount
    ? `${filteredCount} of ${totalCount}`
    : `${totalCount} messages`
  return (
    <div className="flex items-center gap-2">
      <select
        value={filters.type ?? ''}
        onChange={onTypeChange}
        aria-label="Filter by message type"
        className={SELECT_CLASSES}
      >
        <option value="">All types</option>
        {MESSAGE_TYPES.map((t) => (
          <option key={t} value={t}>
            {getMessageTypeLabel(t)}
          </option>
        ))}
      </select>
      <select
        value={filters.priority ?? ''}
        onChange={onPriorityChange}
        aria-label="Filter by priority"
        className={SELECT_CLASSES}
      >
        <option value="">All priorities</option>
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {capitalize(p)}
          </option>
        ))}
      </select>
      <div className="relative flex-1">
        <Search
          className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          type="text"
          value={filters.search ?? ''}
          onChange={onSearchChange}
          placeholder="Search messages..."
          aria-label="Search messages"
          className={SEARCH_INPUT_CLASSES}
        />
      </div>
      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
        {countLabel}
      </span>
    </div>
  )
}

interface ActiveFilterPillsProps {
  filters: MessagePageFilters
  onFiltersChange: (filters: MessagePageFilters) => void
}

function ActiveFilterPills({ filters, onFiltersChange }: ActiveFilterPillsProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {filters.type && (
        <FilterPill
          label={getMessageTypeLabel(filters.type)}
          onRemove={() => onFiltersChange({ ...filters, type: undefined })}
        />
      )}
      {filters.priority && (
        <FilterPill
          label={capitalize(filters.priority)}
          onRemove={() => onFiltersChange({ ...filters, priority: undefined })}
        />
      )}
      {filters.search && (
        <FilterPill
          label={`"${filters.search}"`}
          onRemove={() => onFiltersChange({ ...filters, search: undefined })}
        />
      )}
      <button
        type="button"
        onClick={() => onFiltersChange({})}
        className="text-[10px] text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Clear all
      </button>
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
        aria-label={`Remove ${label} filter`}
        className="rounded-full p-0.5 hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <X className="size-2.5" />
      </button>
    </span>
  )
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
