import { useMemo } from 'react'
import {
  BRAIN_ENTRY_KIND_VALUES,
  BRAIN_ENTRY_STATUS_VALUES,
  type BrainEntryKind,
  type BrainEntryStatus,
  type BrainSummary,
} from '@/api/types'
import { SkeletonText } from '@/components/ui/skeleton'
import { BRAIN_KIND_HEADING, BRAIN_STATUS_LABEL } from './labels'

export interface BrainEntryListProps {
  entries: readonly BrainSummary[]
  loading: boolean
  hasMore: boolean
  selectedEntryId: string | null
  kindFilter: BrainEntryKind | null
  statusFilter: BrainEntryStatus | null
  onSelect: (entryId: string) => void
  onLoadMore: () => void
  onKindFilterChange: (kind: BrainEntryKind | null) => void
  onStatusFilterChange: (status: BrainEntryStatus | null) => void
}

function groupByKind(
  entries: readonly BrainSummary[],
): readonly (readonly [BrainEntryKind, readonly BrainSummary[]])[] {
  return BRAIN_ENTRY_KIND_VALUES.map(
    (kind) =>
      [kind, entries.filter((entry) => entry.entry_kind === kind)] as const,
  ).filter(([, group]) => group.length > 0)
}

export function BrainEntryList({
  entries,
  loading,
  hasMore,
  selectedEntryId,
  kindFilter,
  statusFilter,
  onSelect,
  onLoadMore,
  onKindFilterChange,
  onStatusFilterChange,
}: BrainEntryListProps) {
  const visible = useMemo(
    () =>
      entries.filter(
        (entry) =>
          (kindFilter === null || entry.entry_kind === kindFilter) &&
          (statusFilter === null || entry.status === statusFilter),
      ),
    [entries, kindFilter, statusFilter],
  )
  const grouped = useMemo(() => groupByKind(visible), [visible])

  return (
    <aside className="border-border flex flex-col gap-3 border-r pr-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by kind">
        <FilterChip
          label="All kinds"
          active={kindFilter === null}
          onClick={() => onKindFilterChange(null)}
        />
        {BRAIN_ENTRY_KIND_VALUES.map((kind) => (
          <FilterChip
            key={kind}
            label={BRAIN_KIND_HEADING[kind]}
            active={kindFilter === kind}
            onClick={() => onKindFilterChange(kind)}
          />
        ))}
      </div>
      <div
        className="flex flex-wrap gap-2"
        role="group"
        aria-label="Filter by status"
      >
        <FilterChip
          label="Any status"
          active={statusFilter === null}
          onClick={() => onStatusFilterChange(null)}
        />
        {BRAIN_ENTRY_STATUS_VALUES.map((status) => (
          <FilterChip
            key={status}
            label={BRAIN_STATUS_LABEL[status]}
            active={statusFilter === status}
            onClick={() => onStatusFilterChange(status)}
          />
        ))}
      </div>
      <BrainListBody
        grouped={grouped}
        loading={loading}
        hasEntries={entries.length > 0}
        hasMore={hasMore}
        selectedEntryId={selectedEntryId}
        onSelect={onSelect}
        onLoadMore={onLoadMore}
      />
    </aside>
  )
}

interface BrainListBodyProps {
  grouped: readonly (readonly [BrainEntryKind, readonly BrainSummary[]])[]
  loading: boolean
  hasEntries: boolean
  hasMore: boolean
  selectedEntryId: string | null
  onSelect: (entryId: string) => void
  onLoadMore: () => void
}

function BrainListBody({
  grouped,
  loading,
  hasEntries,
  hasMore,
  selectedEntryId,
  onSelect,
  onLoadMore,
}: BrainListBodyProps) {
  if (loading && !hasEntries) {
    return <SkeletonText lines={6} className="pt-2" />
  }
  return (
    <>
      {grouped.length === 0 ? (
        <p className="text-muted-foreground text-sm">No matching brain entries.</p>
      ) : (
        grouped.map(([kind, group]) => (
          <BrainKindSection
            key={kind}
            heading={BRAIN_KIND_HEADING[kind]}
            group={group}
            selectedEntryId={selectedEntryId}
            onSelect={onSelect}
          />
        ))
      )}
      {hasMore && (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={loading}
          className="border-border text-foreground/80 hover:bg-muted/50 self-start rounded border px-3 py-1 text-xs disabled:opacity-50"
        >
          {loading ? 'Loading more...' : 'Load more'}
        </button>
      )}
    </>
  )
}

interface BrainKindSectionProps {
  heading: string
  group: readonly BrainSummary[]
  selectedEntryId: string | null
  onSelect: (entryId: string) => void
}

function BrainKindSection({
  heading,
  group,
  selectedEntryId,
  onSelect,
}: BrainKindSectionProps) {
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
        {heading}
      </h2>
      <ul className="flex flex-col gap-1">
        {group.map((entry) => (
          <BrainEntryButton
            key={entry.entry_id}
            entry={entry}
            selected={selectedEntryId === entry.entry_id}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </section>
  )
}

interface BrainEntryButtonProps {
  entry: BrainSummary
  selected: boolean
  onSelect: (entryId: string) => void
}

function BrainEntryButton({ entry, selected, onSelect }: BrainEntryButtonProps) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(entry.entry_id)}
        aria-pressed={selected}
        className={
          selected
            ? 'bg-muted text-foreground w-full rounded px-3 py-2 text-left'
            : 'text-foreground/80 hover:bg-muted/50 w-full rounded px-3 py-2 text-left'
        }
      >
        <span className="block text-sm font-medium">{entry.title}</span>
        <span className="text-muted-foreground block text-xs">
          {BRAIN_STATUS_LABEL[entry.status]}
          {` · r${entry.revision}`}
        </span>
      </button>
    </li>
  )
}

interface FilterChipProps {
  label: string
  active: boolean
  onClick: () => void
}

function FilterChip({ label, active, onClick }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'border-primary bg-primary/10 text-primary rounded-full border px-3 py-1 text-xs'
          : 'border-border text-foreground/80 hover:bg-muted/50 rounded-full border px-3 py-1 text-xs'
      }
    >
      {label}
    </button>
  )
}
