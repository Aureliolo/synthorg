import { create } from 'zustand'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('org-chart-prefs')

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

const PARTICLE_FLOW_MODES = ['always', 'live', 'off'] as const satisfies readonly ParticleFlowMode[]

interface OrgChartPrefs {
  particleFlowMode: ParticleFlowMode
  showAddAgentButton: boolean
  showLeadBadge: boolean
  showBudgetBar: boolean
  showStatusDots: boolean
  showMinimap: boolean
}

interface OrgChartPrefsState extends OrgChartPrefs {
  /** True once preferences have been hydrated from the backend at least once. */
  hydrated: boolean
  setParticleFlowMode: (mode: ParticleFlowMode) => void
  setShowAddAgentButton: (show: boolean) => void
  setShowLeadBadge: (show: boolean) => void
  setShowBudgetBar: (show: boolean) => void
  setShowStatusDots: (show: boolean) => void
  setShowMinimap: (show: boolean) => void
  /**
   * Load org-chart view preferences from the backend (`org_chart` settings
   * namespace) and apply them. The dashboard is a pure API consumer: the
   * backend is the source of truth and there is no client-side copy, so this
   * runs when the org chart mounts. Failures degrade to the defaults.
   */
  hydrate: () => Promise<void>
}

const DEFAULTS: OrgChartPrefs = {
  // Default to 'live' -- particles only animate on edges with recent message
  // activity, so an idle org looks calm and edges light up when work flows.
  particleFlowMode: 'live',
  showAddAgentButton: true,
  showLeadBadge: true,
  showBudgetBar: true,
  // Off by default -- with particle flow also on, both at once reads as noise.
  showStatusDots: false,
  // Off by default -- users explicitly opt in via the toolbar toggle.
  showMinimap: false,
}

// Map each preference to its backend `org_chart.*` settings key (snake_case).
const BACKEND_KEY = {
  particleFlowMode: 'particle_flow_mode',
  showAddAgentButton: 'show_add_agent_button',
  showLeadBadge: 'show_lead_badge',
  showBudgetBar: 'show_budget_bar',
  showStatusDots: 'show_status_dots',
  showMinimap: 'show_minimap',
} as const satisfies Record<keyof OrgChartPrefs, string>

const FRONTEND_KEY: Record<string, keyof OrgChartPrefs> = {
  particle_flow_mode: 'particleFlowMode',
  show_add_agent_button: 'showAddAgentButton',
  show_lead_badge: 'showLeadBadge',
  show_budget_bar: 'showBudgetBar',
  show_status_dots: 'showStatusDots',
  show_minimap: 'showMinimap',
}

function isParticleFlowMode(value: string): value is ParticleFlowMode {
  return (PARTICLE_FLOW_MODES as readonly string[]).includes(value)
}

/** Persist a single org-chart preference to the backend; toast on failure. */
async function persistPref(key: keyof OrgChartPrefs, value: string): Promise<void> {
  try {
    await updateSetting('org_chart', BACKEND_KEY[key], { value })
  } catch (err) {
    log.error('Failed to save org-chart preference:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to save org chart preference'),
      description: getErrorMessage(err),
    })
  }
}

function persistBool(key: keyof OrgChartPrefs, value: boolean): void {
  void persistPref(key, value ? 'true' : 'false')
}

export const useOrgChartPrefs = create<OrgChartPrefsState>()((set) => ({
  ...DEFAULTS,
  hydrated: false,

  setParticleFlowMode: (mode) => {
    set({ particleFlowMode: mode })
    void persistPref('particleFlowMode', mode)
  },
  setShowAddAgentButton: (show) => {
    set({ showAddAgentButton: show })
    persistBool('showAddAgentButton', show)
  },
  setShowLeadBadge: (show) => {
    set({ showLeadBadge: show })
    persistBool('showLeadBadge', show)
  },
  setShowBudgetBar: (show) => {
    set({ showBudgetBar: show })
    persistBool('showBudgetBar', show)
  },
  setShowStatusDots: (show) => {
    set({ showStatusDots: show })
    persistBool('showStatusDots', show)
  },
  setShowMinimap: (show) => {
    set({ showMinimap: show })
    persistBool('showMinimap', show)
  },

  hydrate: async (): Promise<void> => {
    try {
      const entries = await getNamespaceSettings('org_chart')
      const patch: Partial<OrgChartPrefs> = {}
      for (const entry of entries) {
        const key = FRONTEND_KEY[entry.definition.key]
        if (key === undefined) continue
        if (key === 'particleFlowMode') {
          if (isParticleFlowMode(entry.value)) patch.particleFlowMode = entry.value
        } else {
          patch[key] = entry.value === 'true'
        }
      }
      set({ ...patch, hydrated: true })
    } catch (err) {
      log.warn('Failed to hydrate org-chart prefs from backend, using defaults:', getErrorMessage(err))
      set({ hydrated: true })
    }
  },
}))

/**
 * Reset the singleton store to defaults. The store no longer persists to
 * ``localStorage`` (the backend is the source of truth), so the only cross-test
 * leak is in-memory state in a shared Vitest worker; the global ``afterEach``
 * in ``test-setup.tsx`` calls this to clear it.
 */
export function resetOrgChartPrefs(): void {
  useOrgChartPrefs.setState({ ...DEFAULTS, hydrated: false })
}
