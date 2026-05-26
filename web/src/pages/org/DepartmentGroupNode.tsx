import { memo, type MouseEvent as ReactMouseEvent } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { ChevronDown, ChevronRight, Plus, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { StatusBadge } from '@/components/ui/status-badge'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'
import type { DepartmentGroupData } from './build-org-tree'

export type DepartmentGroupType = Node<DepartmentGroupData, 'department'>

/**
 * Per-status ring color for the dept card's status-dot strip. Each dot
 * gets a colored ring on top of `<StatusBadge>`'s semantic bg so it
 * stands out against the dark card background -- the old 6 px gray
 * "idle" dot was nearly invisible, which made the Status Dots toggle
 * look like it did nothing. Bg color is owned by `<StatusBadge>` via
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
  // NO min-h here -- let the layout math in layout.ts drive the
  // rendered size exactly.  Earlier versions clamped the card above
  // the computed height, leaving dead whitespace when toggles were off.
  return cn(
    'relative flex h-full w-full flex-col rounded-xl border p-card transition-colors duration-200',
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
        <span className="truncate font-sans text-xs font-semibold uppercase tracking-wide text-foreground">
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
  budgetPercent: number | null
  utilizationPercent: number | null
}

/**
 * Budget allocation share + seat utilisation. The left label is the
 * dept's share of the total budget pool; the right label + bar is the
 * fraction of agent seats currently active. Shown only when the dept
 * has a budget allocation AND the user enabled the budget bar toggle.
 */
function DeptBudgetBar({ displayName, budgetPercent, utilizationPercent }: DeptBudgetBarProps) {
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  if (!showBudgetBar || budgetPercent === null || budgetPercent <= 0) return null
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
              'h-full rounded-full transition-all duration-300',
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
 * each with a status-color ring and an "<agentId>: <status>" label.
 * Hidden when the user disables dots in the view menu.
 */
function DeptStatusDots({ statusDots }: Pick<DepartmentGroupData, 'statusDots'>) {
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)
  // Clamp the dots row so a huge dept doesn't blow the header width.
  const visibleDots = statusDots.slice(0, MAX_STATUS_DOTS)
  const hiddenDotCount = Math.max(0, statusDots.length - MAX_STATUS_DOTS)
  if (!showStatusDots || visibleDots.length === 0) return null
  return (
    <div className="flex items-center gap-1.5 pt-1" aria-label="Agent status overview">
      {visibleDots.map((dot) => (
        <StatusBadge
          key={dot.agentId}
          status={dot.runtimeStatus}
          dotClassName={cn('size-2.5 ring-2', STATUS_DOT_RING[dot.runtimeStatus])}
          ariaLabel={`${dot.agentId}: ${dot.runtimeStatus}`}
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
function DeptEmptyState({ isEmpty }: { isEmpty: boolean }) {
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
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
function DeptAddAgentChip({ isEmpty }: { isEmpty: boolean }) {
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
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

function DepartmentGroupNodeComponent({ id, data }: NodeProps<DepartmentGroupType>) {
  const {
    displayName,
    agentCount,
    budgetPercent,
    utilizationPercent,
    statusDots,
    isEmpty,
    isDropTarget,
    isCollapsed,
    onToggleCollapsed,
  } = data

  // `h-full w-full` makes the visible border span the full size React
  // Flow reserved on the outer wrapper -- otherwise the border would
  // only wrap the header and the child agent cards would sit OUTSIDE.
  return (
    <div
      className={deptCardClassName(isDropTarget, isEmpty)}
      data-testid="department-group-node"
      aria-label={deptAriaLabel(displayName, agentCount)}
    >
      {/* Hidden target handle (top) receives incoming edges; hidden
          source handle (bottom) emits root-dept -> other-dept edges.
          Both are visually invisible so lines terminate at the box. */}
      <Handle type="target" position={Position.Top} className="!size-0 !border-0 !bg-transparent" />
      <Handle type="source" position={Position.Bottom} className="!size-0 !border-0 !bg-transparent" />

      <div className="space-y-1.5">
        <DeptCardHeader
          id={id}
          displayName={displayName}
          agentCount={agentCount}
          isEmpty={isEmpty}
          isCollapsed={isCollapsed}
          onToggleCollapsed={onToggleCollapsed}
        />
        <DeptBudgetBar
          displayName={displayName}
          budgetPercent={budgetPercent}
          utilizationPercent={utilizationPercent}
        />
        <DeptStatusDots statusDots={statusDots} />
      </div>

      <DeptEmptyState isEmpty={isEmpty} />
      <DeptAddAgentChip isEmpty={isEmpty} />
    </div>
  )
}

export const DepartmentGroupNode = memo(DepartmentGroupNodeComponent)
