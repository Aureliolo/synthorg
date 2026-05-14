import { useCallback } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getMessageTypeLabel } from '@/utils/messages'
import type { MessagePageFilters } from '@/utils/messages'
import type { MessagePriority, MessageType } from '@/api/types/messages'

const MESSAGE_TYPES: MessageType[] = [
  'task_update', 'question', 'announcement', 'review_request', 'approval',
  'delegation', 'status_report', 'escalation', 'meeting_contribution', 'hr_notification',
]

const PRIORITIES: MessagePriority[] = ['low', 'normal', 'high', 'urgent']

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
      const trimmed = e.target.value.trim()
      onFiltersChange({
        ...filters,
        search: trimmed || undefined,
      })
    },
    [filters, onFiltersChange],
  )

  const hasFilters = !!(filters.type || filters.priority || filters.search)
  const showFilteredCount = hasFilters && filteredCount !== undefined

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {/* Type filter */}
        <select
          value={filters.type ?? ''}
          onChange={handleTypeChange}
          aria-label="Filter by message type"
          className={cn(
            'h-7 rounded-md border border-border bg-surface px-2 text-xs text-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
          )}
        >
          <option value="">All types</option>
          {MESSAGE_TYPES.map((t) => (
            <option key={t} value={t}>{getMessageTypeLabel(t)}</option>
          ))}
        </select>

        {/* Priority filter */}
        <select
          value={filters.priority ?? ''}
          onChange={handlePriorityChange}
          aria-label="Filter by priority"
          className={cn(
            'h-7 rounded-md border border-border bg-surface px-2 text-xs text-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
          )}
        >
          <option value="">All priorities</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
          ))}
        </select>

        {/* Search */}
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            type="text"
            value={filters.search ?? ''}
            onChange={handleSearchChange}
            placeholder="Search messages..."
            aria-label="Search messages"
            className={cn(
              'h-7 w-full rounded-md border border-border bg-surface pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
            )}
          />
        </div>

        {/* Count */}
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
          {showFilteredCount ? `${filteredCount} of ${totalCount}` : `${totalCount} messages`}
        </span>
      </div>

      {/* Active filter pills */}
      {hasFilters && (
        <div className="flex flex-wrap items-center gap-1.5">
          {filters.type && (
            <FilterPill
              label={getMessageTypeLabel(filters.type)}
              onRemove={() => onFiltersChange({ ...filters, type: undefined })}
            />
          )}
          {filters.priority && (
            <FilterPill
              label={filters.priority.charAt(0).toUpperCase() + filters.priority.slice(1)}
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
        aria-label={`Remove ${label} filter`}
        className="rounded-full p-0.5 hover:bg-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <X className="size-2.5" />
      </button>
    </span>
  )
}
