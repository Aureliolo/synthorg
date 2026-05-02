import { useCallback, useMemo, useState } from 'react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { SectionCard } from '@/components/ui/section-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EmptyState } from '@/components/ui/empty-state'
import { formatCurrency } from '@/utils/format'
import { ArrowDown, ArrowUp, Users } from 'lucide-react'
import type { AgentSpendingRow } from '@/utils/budget'

type SortKey = 'agentName' | 'totalCost' | 'budgetPercent' | 'taskCount' | 'costPerTask'
type SortDirection = 'asc' | 'desc'

export interface AgentSpendingTableProps {
  rows: readonly AgentSpendingRow[]
  currency?: string
}

// Proportional column widths (character-based) so the table grows with
// the viewport instead of clamping to fixed pixel widths that overflow
// on narrower screens. ``flex-1`` for the name column lets it absorb
// available space; numeric columns size to a sensible character cap.
// Defined once so header + body cells reference the same source; drift
// between the two would silently misalign columns.
const COLUMN_WIDTHS: Record<SortKey, string> = {
  agentName: 'flex-1 min-w-[12ch]',
  totalCost: 'min-w-[10ch]',
  budgetPercent: 'min-w-[8ch]',
  taskCount: 'min-w-[6ch]',
  costPerTask: 'min-w-[10ch]',
}

const COLUMNS: { key: SortKey; label: string; width: string; sortable: boolean }[] = [
  { key: 'agentName', label: 'Agent', width: COLUMN_WIDTHS.agentName, sortable: true },
  { key: 'totalCost', label: 'Total Cost', width: COLUMN_WIDTHS.totalCost, sortable: true },
  { key: 'budgetPercent', label: '% of Budget', width: COLUMN_WIDTHS.budgetPercent, sortable: true },
  { key: 'taskCount', label: 'Tasks', width: COLUMN_WIDTHS.taskCount, sortable: true },
  { key: 'costPerTask', label: 'Cost/Task', width: COLUMN_WIDTHS.costPerTask, sortable: true },
]

function compareRows(
  a: AgentSpendingRow,
  b: AgentSpendingRow,
  key: SortKey,
  dir: SortDirection,
): number {
  let cmp = 0
  switch (key) {
    case 'agentName': cmp = a.agentName.localeCompare(b.agentName); break
    case 'totalCost': cmp = a.totalCost - b.totalCost; break
    case 'budgetPercent': cmp = a.budgetPercent - b.budgetPercent; break
    case 'taskCount': cmp = a.taskCount - b.taskCount; break
    case 'costPerTask': cmp = a.costPerTask - b.costPerTask; break
  }
  return dir === 'desc' ? -cmp : cmp
}

function ColumnHeader({ col, sortKey, sortDir, onSort }: {
  col: typeof COLUMNS[number]
  sortKey: SortKey
  sortDir: SortDirection
  onSort: (key: SortKey) => void
}) {
  return (
    <button
      type="button"
      onClick={() => col.sortable && onSort(col.key)}
      className={cn(
        'flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted transition-colors focus-visible:text-foreground',
        FOCUS_RING,
        col.sortable && 'cursor-pointer hover:text-foreground',
        col.width,
        col.key !== 'agentName' && 'justify-end',
      )}
      aria-sort={sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      {col.label}
      {sortKey === col.key && (
        sortDir === 'asc'
          ? <ArrowUp className="size-3" aria-hidden="true" />
          : <ArrowDown className="size-3" aria-hidden="true" />
      )}
    </button>
  )
}

function SpendingRow({ row, currency }: {
  row: AgentSpendingRow
  currency?: string
}) {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <span className={cn(COLUMN_WIDTHS.agentName, 'truncate text-[13px] font-medium text-foreground')}>
        {row.agentName}
      </span>
      <span className={cn(COLUMN_WIDTHS.totalCost, 'text-right font-mono text-xs text-foreground')}>
        {formatCurrency(row.totalCost, currency)}
      </span>
      <span className={cn(COLUMN_WIDTHS.budgetPercent, 'text-right font-mono text-xs text-text-secondary')}>
        {row.budgetPercent.toFixed(1)}%
      </span>
      <span className={cn(COLUMN_WIDTHS.taskCount, 'text-right font-mono text-xs text-text-secondary')}>
        {row.taskCount}
      </span>
      <span className={cn(COLUMN_WIDTHS.costPerTask, 'text-right font-mono text-xs text-text-muted')}>
        {formatCurrency(row.costPerTask, currency)}
      </span>
    </div>
  )
}

export function AgentSpendingTable({ rows, currency }: AgentSpendingTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('totalCost')
  const [sortDir, setSortDir] = useState<SortDirection>('desc')

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'agentName' ? 'asc' : 'desc')
    }
  }, [sortKey])

  const sorted = useMemo(
    () => [...rows].sort((a, b) => compareRows(a, b, sortKey, sortDir)),
    [rows, sortKey, sortDir],
  )

  return (
    <SectionCard title="Agent Spending" icon={Users}>
      {rows.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No agent spending data"
          description="Cost records will appear as agents consume tokens"
        />
      ) : (
        // Outer overflow-x-auto so on narrow viewports (phones, tight
        // dashboard splits) the table scrolls instead of overflowing
        // the section card. The inner min-w sizes the row to its
        // natural width so the proportional column widths still hold.
        <div className="overflow-x-auto rounded-lg border border-border">
          <div className="md:min-w-[40rem]">
            <div className="flex items-center gap-4 border-b border-border bg-surface px-4 py-2">
              {COLUMNS.map((col) => (
                <ColumnHeader key={col.key} col={col} sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              ))}
            </div>

            <StaggerGroup className="divide-y divide-border">
              {sorted.map((row) => (
                <StaggerItem key={row.agentId}>
                  <SpendingRow row={row} currency={currency} />
                </StaggerItem>
              ))}
            </StaggerGroup>
          </div>
        </div>
      )}
    </SectionCard>
  )
}
