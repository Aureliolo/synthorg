import { memo, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { ChevronDown, ChevronRight, Plus, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import { StatusBadge } from '@/components/ui/status-badge'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import type { DepartmentGroupData } from './build-org-tree'
import {
  DEPT_HEADER_ROW_GAP,
  DEPT_HEADER_ROW_HEIGHT,
  type DeptHeaderInputs,
  type DeptHeaderRowKind,
  deptHeaderRows,
} from './card-metrics'
import { DepartmentStatsBar } from './DepartmentStatsBar'

export type DepartmentGroupType = Node<DepartmentGroupData, 'department'>

/**
 * Per-status ring colour for the dept card's status-dot strip. A dot at this
 * size carries too little area for its fill alone to read against the card, and
 * `idle` is the worst case: a grey dot on a dark card is invisible enough that
 * the Status Dots toggle looks like it does nothing. The ring is what makes it
 * legible. Background colour is owned by `<StatusBadge>` via
 * `getStatusColor(status)`; only the ring class lives here.
 */
const STATUS_DOT_RING: Record<AgentRuntimeStatus, string> = {
  active: 'ring-success/30',
  idle: 'ring-accent/30',
  error: 'ring-danger/30',
  offline: 'ring-text-muted/30',
}

const MAX_STATUS_DOTS = 10

function deptCardClassName(isDropTarget: boolean | undefined, isEmpty: boolean): string {
  // Deliberately no `min-h`: the layout computes this card's height from
  // `card-metrics.ts` and reserves exactly that, so a floor here would clamp
  // the card above the reservation and leave dead whitespace inside it
  // whenever the toggles are off.
  return cn(
    'relative flex h-full w-full flex-col rounded-xl border p-card transition-colors duration-[var(--so-transition-default)]',
    'min-w-[220px]',
    isDropTarget && 'border-accent bg-accent/5',
    !isDropTarget && isEmpty && 'border-dashed border-border bg-card/20',
    !isDropTarget && !isEmpty && 'border-border bg-card/40',
  )
}

function deptAriaLabel(displayName: string, agentCount: number): string {
  if (agentCount === 0) return `Department: ${displayName}, empty`
  const noun = agentCount === 1 ? 'agent' : 'agents'
  return `Department: ${displayName}, ${agentCount} ${noun}`
}

function utilizationTextClass(pct: number): string {
  if (pct >= 90) return 'text-danger'
  if (pct >= 75) return 'text-warning'
  return ''
}

function utilizationBarClass(pct: number): string {
  if (pct >= 90) return 'bg-danger'
  if (pct >= 75) return 'bg-warning'
  return 'bg-accent'
}

interface DeptCardHeaderProps {
  id: string
  displayName: string
  agentCount: number
  isEmpty: boolean
  isCollapsed: boolean | undefined
  onToggleCollapsed: ((deptId: string) => void) | undefined
}

/** Title row: collapse chevron + dept name + agent count pill. */
function DeptCardHeader({
  id,
  displayName,
  agentCount,
  isEmpty,
  isCollapsed,
  onToggleCollapsed,
}: DeptCardHeaderProps) {
  const canCollapse = !isEmpty && onToggleCollapsed != null
  const handleToggleClick = (e: ReactMouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    onToggleCollapsed?.(id)
  }
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-1.5">
        {canCollapse && (
          <button
            type="button"
            onClick={handleToggleClick}
            className="shrink-0 rounded p-0.5 text-text-muted transition-colors hover:bg-border/40 hover:text-foreground"
            aria-label={isCollapsed ? `Expand ${displayName}` : `Collapse ${displayName}`}
            aria-expanded={!isCollapsed}
          >
            {isCollapsed ? (
              <ChevronRight className="size-3" aria-hidden="true" />
            ) : (
              <ChevronDown className="size-3" aria-hidden="true" />
            )}
          </button>
        )}
        <span
          className="truncate font-sans text-xs font-semibold uppercase tracking-wide text-foreground"
          title={displayName}
        >
          {displayName}
        </span>
      </div>
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-background px-1.5 py-0.5 font-mono text-micro font-medium text-text-secondary">
        <Users className="size-2.5" aria-hidden="true" />
        {agentCount}
      </span>
    </div>
  )
}

interface DeptBudgetBarProps {
  displayName: string
  /** Non-null by construction: `deptHeaderRows` only lists this row when set. */
  budgetPercent: number
  utilizationPercent: number | null
}

/**
 * Budget allocation share + seat utilisation. The left label is the
 * dept's share of the total budget pool; the right label + bar is the
 * fraction of agent seats currently active.
 *
 * Whether this row appears at all is `deptHeaderRows`' decision, not this
 * component's: the layout reserves the band from that same list, and a row that
 * gated itself independently is how the reserve and the render came to disagree.
 */
function DeptBudgetBar({ displayName, budgetPercent, utilizationPercent }: DeptBudgetBarProps) {
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between font-mono text-micro text-text-secondary">
        <span>{budgetPercent}% budget</span>
        {utilizationPercent !== null && (
          <span className={cn(utilizationTextClass(utilizationPercent))}>
            {utilizationPercent}% active
          </span>
        )}
      </div>
      {utilizationPercent !== null && (
        <div
          className="h-1 w-full overflow-hidden rounded-full bg-border/40"
          role="meter"
          aria-valuenow={utilizationPercent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${displayName} agent activity`}
        >
          <div
            className={cn(
              'h-full rounded-full transition-all duration-[var(--so-transition-medium)]',
              utilizationBarClass(utilizationPercent),
            )}
            style={{ width: `${utilizationPercent}%` }}
          />
        </div>
      )}
    </div>
  )
}

/**
 * Status dots row -- one `<StatusBadge>` per agent (capped at 10 +N),
 * each with a status-color ring and a "<name>: <status>" label.
 *
 * Whether the row appears is `deptHeaderRows`' decision, so it is not re-tested
 * here. The label names the agent rather than keying them, because a dot is the
 * one part of this card that a screen reader is the only way to read.
 */
function DeptStatusDots({ statusDots }: Pick<DepartmentGroupData, 'statusDots'>) {
  // Clamp the dots row so a huge dept doesn't blow the header width.
  const visibleDots = statusDots.slice(0, MAX_STATUS_DOTS)
  const hiddenDotCount = Math.max(0, statusDots.length - MAX_STATUS_DOTS)
  // `role="group"` because a bare `<div>` maps to `generic`, which prohibits an
  // accessible name: the label would be dropped by every AT.
  return (
    <div
      className="flex items-center gap-1.5 pt-1"
      role="group"
      aria-label="Agent status overview"
    >
      {visibleDots.map((dot) => (
        <StatusBadge
          key={dot.agentId}
          status={dot.runtimeStatus}
          dotClassName={cn('size-2.5 ring-2', STATUS_DOT_RING[dot.runtimeStatus])}
          ariaLabel={`${dot.agentName}: ${dot.runtimeStatus}`}
        />
      ))}
      {hiddenDotCount > 0 && (
        <span className="font-mono text-micro text-text-muted">+{hiddenDotCount}</span>
      )}
    </div>
  )
}

/**
 * Empty-state CTA. Always shows the "No agents yet" icon + label so an
 * empty dept is never blank; the "+ Add agent" chip is gated on the
 * view-menu toggle. `flex-1` centres the stack in the remaining space.
 */
function DeptEmptyState({
  isEmpty,
  showAddAgentButton,
}: {
  isEmpty: boolean
  showAddAgentButton: boolean
}) {
  if (!isEmpty) return null
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 pb-2 text-text-muted">
      <Users className="size-5" aria-hidden="true" />
      <span className="font-sans text-xs">No agents yet</span>
      {showAddAgentButton && (
        <span className="inline-flex items-center gap-1 rounded-md border border-border bg-background/50 px-2 py-1 text-micro text-text-secondary">
          <Plus className="size-3" aria-hidden="true" />
          Add agent
        </span>
      )}
    </div>
  )
}

/** Inline "+ Add agent" chip pinned to the bottom of a populated card. */
function DeptAddAgentChip({
  isEmpty,
  showAddAgentButton,
}: {
  isEmpty: boolean
  showAddAgentButton: boolean
}) {
  if (isEmpty || !showAddAgentButton) return null
  return (
    <div className="mt-auto flex items-center justify-center pt-5">
      <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-border bg-background/30 px-2 py-0.5 text-micro text-text-muted">
        <Plus className="size-3" aria-hidden="true" />
        Add agent
      </span>
    </div>
  )
}

/**
 * The header band, laid out from the row list the layout also reserves from.
 *
 * Each row is given its own height from `DEPT_HEADER_ROW_HEIGHT` rather than
 * sizing to its content. That is what makes the band's height a fact both sides
 * already agree on: the reserve is the sum of these same numbers, so no restyle
 * can push the band past what the layout left for it and onto the agent cards.
 */
function DeptHeaderRow({
  kind,
  id,
  data,
}: {
  kind: DeptHeaderRowKind
  id: string
  data: DepartmentGroupData
}): ReactNode {
  switch (kind) {
    case 'title':
      return (
        <DeptCardHeader
          id={id}
          displayName={data.displayName}
          agentCount={data.agentCount}
          isEmpty={data.isEmpty}
          isCollapsed={data.isCollapsed}
          onToggleCollapsed={data.onToggleCollapsed}
        />
      )
    case 'budget':
      return (
        <DeptBudgetBar
          displayName={data.displayName}
          budgetPercent={data.budgetPercent ?? 0}
          utilizationPercent={data.utilizationPercent}
        />
      )
    case 'dots':
      return <DeptStatusDots statusDots={data.statusDots} />
    case 'stats':
      return (
        <DepartmentStatsBar
          activeCount={data.activeCount}
          cost7d={data.cost7d}
          {...(data.currency !== null && { currency: data.currency })}
        />
      )
  }
}

function DeptCardHeaderBlock({
  id,
  data,
  inputs,
}: {
  id: string
  data: DepartmentGroupData
  inputs: DeptHeaderInputs
}) {
  return (
    <div className="flex flex-col" style={{ gap: DEPT_HEADER_ROW_GAP }}>
      {deptHeaderRows(inputs).map((kind) => (
        <div key={kind} style={{ height: DEPT_HEADER_ROW_HEIGHT[kind] }}>
          <DeptHeaderRow kind={kind} id={id} data={data} />
        </div>
      ))}
    </div>
  )
}

function DepartmentGroupNodeComponent({ id, data }: NodeProps<DepartmentGroupType>) {
  const { displayName, agentCount, isEmpty, isDropTarget } = data
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
  const headerInputs: DeptHeaderInputs = {
    showBudgetBar,
    showStatusDots,
    showAddAgentButton,
    budgetPercent: data.budgetPercent,
    statusDotCount: data.statusDots.length,
    isEmpty,
    isCollapsed: data.isCollapsed ?? false,
  }

  // `h-full w-full` makes the visible border span the full size React
  // Flow reserved on the outer wrapper -- otherwise the border would
  // only wrap the header and the child agent cards would sit OUTSIDE.
  return (
    <div
      className={deptCardClassName(isDropTarget, isEmpty)}
      data-testid="department-group-node"
      role="group"
      aria-label={deptAriaLabel(displayName, agentCount)}
    >
      {/* Hidden target handle (top) receives incoming edges; hidden
          source handle (bottom) emits root-dept -> other-dept edges.
          Both are visually invisible so lines terminate at the box. */}
      <Handle type="target" position={Position.Top} className="!size-0 !border-0 !bg-transparent" />
      <Handle type="source" position={Position.Bottom} className="!size-0 !border-0 !bg-transparent" />

      <DeptCardHeaderBlock id={id} data={data} inputs={headerInputs} />

      {/* The toggle is read once for the whole card: three subscriptions to one
          store field re-render this node three times for a single flip. */}
      <DeptEmptyState isEmpty={isEmpty} showAddAgentButton={showAddAgentButton} />
      <DeptAddAgentChip isEmpty={isEmpty} showAddAgentButton={showAddAgentButton} />
    </div>
  )
}

export const DepartmentGroupNode = memo(DepartmentGroupNodeComponent)
