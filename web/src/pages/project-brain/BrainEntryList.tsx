import { useMemo } from 'react'
import {
  BRAIN_ENTRY_KIND_VALUES,
  BRAIN_ENTRY_STATUS_VALUES,
  type BrainEntryKind,
  type BrainEntryStatus,
  type BrainSummary,
} from '@/api/types'

const KIND_LABEL: Record<BrainEntryKind, string> = {
  decision: 'Decisions',
  open_question: 'Open questions',
  blocker: 'Blockers',
  risk: 'Risks',
  dependency: 'Dependencies',
  plan_revision: 'Plan',
}

const STATUS_LABEL: Record<BrainEntryStatus, string> = {
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

export interface BrainEntryListProps {
  entries: readonly BrainSummary[]
  selectedEntryId: string | null
  kindFilter: BrainEntryKind | null
  statusFilter: BrainEntryStatus | null
  onSelect: (entryId: string) => void
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
  selectedEntryId,
  kindFilter,
  statusFilter,
  onSelect,
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
      <div className="flex flex-wrap gap-2">
        <FilterChip
          label="All kinds"
          active={kindFilter === null}
          onClick={() => onKindFilterChange(null)}
        />
        {BRAIN_ENTRY_KIND_VALUES.map((kind) => (
          <FilterChip
            key={kind}
            label={KIND_LABEL[kind]}
            active={kindFilter === kind}
            onClick={() => onKindFilterChange(kind)}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <FilterChip
          label="Any status"
          active={statusFilter === null}
          onClick={() => onStatusFilterChange(null)}
        />
        {BRAIN_ENTRY_STATUS_VALUES.map((status) => (
          <FilterChip
            key={status}
            label={STATUS_LABEL[status]}
            active={statusFilter === status}
            onClick={() => onStatusFilterChange(status)}
          />
        ))}
      </div>
      {grouped.length === 0 ? (
        <p className="text-muted-foreground text-sm">No matching brain entries.</p>
      ) : (
        grouped.map(([kind, group]) => (
          <BrainKindSection
            key={kind}
            heading={KIND_LABEL[kind]}
            group={group}
            selectedEntryId={selectedEntryId}
            onSelect={onSelect}
          />
        ))
      )}
    </aside>
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
          {STATUS_LABEL[entry.status]}
          {` ${'·'} r${entry.revision}`}
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
