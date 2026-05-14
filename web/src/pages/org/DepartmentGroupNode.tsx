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

  // Visual toggles live in the dedicated prefs store so the
  // OrgChartViewMenu popover can flip them per-user.  Each selector
  // subscribes narrowly so toggling one preference doesn't re-render
  // every dept card for the others.
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)

  const handleToggleClick = (e: ReactMouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    onToggleCollapsed?.(id)
  }

  const canCollapse = !isEmpty && onToggleCollapsed != null

  // Clamp the dots row at MAX_STATUS_DOTS so a huge dept doesn't blow
  // the header width; extra agents are summarised with "+N".
  const visibleDots = statusDots.slice(0, MAX_STATUS_DOTS)
  const hiddenDotCount = Math.max(0, statusDots.length - MAX_STATUS_DOTS)

  return (
    /*
     * `h-full w-full` makes the visible border span the full size
     * React Flow reserved on the outer wrapper -- otherwise the
     * border would only wrap the header content and the child agent
     * cards positioned lower down would appear OUTSIDE the box.
     */
    <div
      className={cn(
        'relative flex h-full w-full flex-col rounded-xl border p-card transition-colors duration-200',
        'min-w-[220px]',
        // NO min-h here -- let the layout math in layout.ts drive
        // the rendered size exactly.  Earlier versions had
        // min-h-[140px]/[180px] which clamped the card above the
        // computed height, leaving dead whitespace inside the box
        // when toggles were off.
        isDropTarget && 'border-accent bg-accent/5',
        !isDropTarget && isEmpty && 'border-dashed border-border bg-card/20',
        !isDropTarget && !isEmpty && 'border-border bg-card/40',
      )}
      data-testid="department-group-node"
      aria-label={`Department: ${displayName}${agentCount > 0 ? `, ${agentCount} ${agentCount === 1 ? 'agent' : 'agents'}` : ', empty'}`}
    >
      {/*
       * Hidden target handle on top -- receives incoming edges from
       * the owner (for the root dept) or from the root dept box
       * (for other depts).  Visually invisible; the line appears to
       * terminate at the box border.
       */}
      <Handle type="target" position={Position.Top} className="!size-0 !border-0 !bg-transparent" />

      {/*
       * Hidden source handle on bottom -- emits outgoing edges from
       * the root dept box down to all other dept boxes.  Non-root
       * depts don't use it but the handle has zero visual cost.
       */}
      <Handle type="source" position={Position.Bottom} className="!size-0 !border-0 !bg-transparent" />

      <div className="space-y-1.5">
        {/* Title row: collapse chevron + dept name + agent count pill */}
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

        {/* Budget allocation share + seat utilisation: the left label
            is the dept's share of the total budget pool, the right
            label is the fraction of agent seats currently active
            (active_agent_count / agent_count). The bar visualises the
            seat-utilisation value, not budget spend. Only shown when
            the dept has a budget allocation configured AND the user
            has the budget bar toggle enabled. */}
        {showBudgetBar && budgetPercent !== null && budgetPercent > 0 && (
          <div className="space-y-0.5">
            <div className="flex items-center justify-between font-mono text-micro text-text-secondary">
              <span>{budgetPercent}% budget</span>
              {utilizationPercent !== null && (
                <span
                  className={cn(
                    utilizationPercent >= 90 && 'text-danger',
                    utilizationPercent >= 75 && utilizationPercent < 90 && 'text-warning',
                  )}
                >
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
                    utilizationPercent >= 90 && 'bg-danger',
                    utilizationPercent >= 75 && utilizationPercent < 90 && 'bg-warning',
                    utilizationPercent < 75 && 'bg-accent',
                  )}
                  style={{ width: `${utilizationPercent}%` }}
                />
              </div>
            )}
          </div>
        )}

        {/* Status dots row -- one dot per agent (capped at 10 +N).
            Each dot is a `<StatusBadge>` with a status-color ring
            from `STATUS_DOT_RING` and an `aria-label` that surfaces
            "<agentId>: <status>" to assistive tech.  Hidden when
            the user disables dots in the view menu. */}
        {showStatusDots && visibleDots.length > 0 && (
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
        )}
      </div>

      {/*
       * Empty-state call to action.  Always shows "No agents yet"
       * icon+label so the empty dept is never blank; the "+ Add
       * agent" chip is only rendered when the user has that toggle
       * enabled in the view menu.  `flex-1` fills the remaining
       * card space so the stack is vertically centered instead of
       * dangling below the border.
       */}
      {isEmpty && (
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
      )}

      {/*
       * Inline "+ Add agent" chip for POPULATED dept cards.  Pinned
       * to the bottom of the card (below all member agents) via
       * `mt-auto`.  `pt-5` gives the chip breathing room from the
       * last member agent above it -- earlier `pt-2` was too tight
       * and made the chip feel glued to the agent card.
       */}
      {!isEmpty && showAddAgentButton && (
        <div className="mt-auto flex items-center justify-center pt-5">
          <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-border bg-background/30 px-2 py-0.5 text-micro text-text-muted">
            <Plus className="size-3" aria-hidden="true" />
            Add agent
          </span>
        </div>
      )}
    </div>
  )
}

export const DepartmentGroupNode = memo(DepartmentGroupNodeComponent)
