import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ORG_CHART_PREFS_PERSIST_NAME } from './org-chart-prefs-teardown'

/**
 * Particle-flow mode for the Org Chart's hierarchy edges.
 *
 * - `always`: particles continuously animate along every edge.
 *   Good for a lively "this org is alive" feel but can be
 *   distracting.
 * - `live`: particles only animate on edges that have seen real
 *   activity recently (messages, task hand-offs).  Quiet edges are
 *   static.  Reflects actual communication patterns.
 * - `off`: no particles, fully static lines.  Quietest option.
 */
export type ParticleFlowMode = 'always' | 'live' | 'off'

/**
 * How long (in milliseconds) an edge stays "active" after the most
 * recent message/task event that touched it, in `live` mode.
 */
export const LIVE_EDGE_ACTIVE_MS = 3000

interface OrgChartPrefsState {
  /** Animation mode for the hierarchy edge particles. */
  particleFlowMode: ParticleFlowMode
  /** Whether to show the inline "+ Add agent" affordance on department cards. */
  showAddAgentButton: boolean
  /** Whether to show the "LEAD" badge on the dept-head agent. */
  showLeadBadge: boolean
  /** Whether to show the budget percent + utilization bar on dept cards. */
  showBudgetBar: boolean
  /** Whether to show the per-agent status dots row on dept cards. */
  showStatusDots: boolean
  /** Whether the minimap is visible in the bottom-right corner. */
  showMinimap: boolean

  setParticleFlowMode: (mode: ParticleFlowMode) => void
  setShowAddAgentButton: (show: boolean) => void
  setShowLeadBadge: (show: boolean) => void
  setShowBudgetBar: (show: boolean) => void
  setShowStatusDots: (show: boolean) => void
  setShowMinimap: (show: boolean) => void
}

export const useOrgChartPrefs = create<OrgChartPrefsState>()(
  persist(
    (set) => ({
      // Default to 'live' -- particles only animate on edges
      // with recent message activity, so an idle org looks calm
      // and the edges light up when work actually flows through
      // them.  Users can still switch to 'always' or 'off' via
      // the toolbar.
      particleFlowMode: 'live',
      showAddAgentButton: true,
      showLeadBadge: true,
      showBudgetBar: true,
      // Status dots are off by default -- with particle flow also
      // on by default, enabling both produced visual noise that
      // users read as "broken lines".  Dots remain available via
      // the toolbar toggle for operators who actually want to see
      // per-agent runtime state on the chart.
      showStatusDots: false,
      // Minimap is off by default -- users explicitly opt in via
      // the toolbar toggle.  Their choice is persisted after that
      // so it only prompts once.
      showMinimap: false,

      setParticleFlowMode: (mode) => set({ particleFlowMode: mode }),
      setShowAddAgentButton: (show) => set({ showAddAgentButton: show }),
      setShowLeadBadge: (show) => set({ showLeadBadge: show }),
      setShowBudgetBar: (show) => set({ showBudgetBar: show }),
      setShowStatusDots: (show) => set({ showStatusDots: show }),
      setShowMinimap: (show) => set({ showMinimap: show }),
    }),
    {
      name: ORG_CHART_PREFS_PERSIST_NAME,
      // Internal Zustand-persist schema marker.  NOT a product
      // version -- this is just the localStorage cache invalidation
      // key.  Bumping it makes the middleware discard any old
      // stored shape and fall back to the initial state above.
      // Keep at 1 during dev; use `migrate` for future schema
      // changes rather than incrementing this.
      version: 1,
    },
  ),
)
