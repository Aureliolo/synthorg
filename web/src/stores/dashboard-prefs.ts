import { create } from 'zustand'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('dashboard-prefs')

/** Max recent command-palette ids retained. */
const MAX_RECENT = 5

interface DashboardPrefs {
  sidebarCollapsed: boolean
  commandRecents: readonly string[]
  settingsAdvancedMode: boolean
  settingsAdvancedWarned: boolean
  tunnelIntroAcknowledged: boolean
  postSetupGuidanceDismissed: boolean
}

interface DashboardPrefsState extends DashboardPrefs {
  /** True once preferences have been hydrated from the backend at least once. */
  hydrated: boolean
  setSidebarCollapsed: (collapsed: boolean) => void
  pushCommandRecent: (commandId: string) => void
  setSettingsAdvancedMode: (advanced: boolean) => void
  markSettingsAdvancedWarned: () => void
  acknowledgeTunnelIntro: () => void
  dismissPostSetupGuidance: () => void
  /**
   * Load dashboard UI preferences from the backend (`dashboard` settings
   * namespace) and apply them. The dashboard is a pure API consumer: the
   * backend is the source of truth, with no client-side copy. Runs once the
   * authed shell mounts; failures degrade to the defaults.
   */
  hydrate: () => Promise<void>
}

const DEFAULTS: DashboardPrefs = {
  sidebarCollapsed: false,
  commandRecents: [],
  settingsAdvancedMode: false,
  settingsAdvancedWarned: false,
  tunnelIntroAcknowledged: false,
  postSetupGuidanceDismissed: false,
}

const BACKEND_KEY = {
  sidebarCollapsed: 'sidebar_collapsed',
  commandRecents: 'command_recents',
  settingsAdvancedMode: 'settings_advanced_mode',
  settingsAdvancedWarned: 'settings_advanced_warned',
  tunnelIntroAcknowledged: 'tunnel_intro_acknowledged',
  postSetupGuidanceDismissed: 'post_setup_guidance_dismissed',
} as const satisfies Record<keyof DashboardPrefs, string>

/** Persist a single preference to the backend; toast on failure. */
async function persist(key: keyof DashboardPrefs, value: string): Promise<void> {
  try {
    await updateSetting('dashboard', BACKEND_KEY[key], { value })
  } catch (err) {
    log.error('Failed to save dashboard preference:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to save dashboard preference'),
      description: getErrorMessage(err),
    })
  }
}

function persistBool(key: keyof DashboardPrefs, value: boolean): void {
  void persist(key, value ? 'true' : 'false')
}

function parseRecents(raw: string): readonly string[] {
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((v): v is string => typeof v === 'string').slice(0, MAX_RECENT)
  } catch {
    return []
  }
}

export const useDashboardPrefs = create<DashboardPrefsState>()((set, get) => ({
  ...DEFAULTS,
  hydrated: false,

  setSidebarCollapsed: (collapsed) => {
    set({ sidebarCollapsed: collapsed })
    persistBool('sidebarCollapsed', collapsed)
  },

  pushCommandRecent: (commandId) => {
    const next = [commandId, ...get().commandRecents.filter((id) => id !== commandId)].slice(
      0,
      MAX_RECENT,
    )
    set({ commandRecents: next })
    void persist('commandRecents', JSON.stringify(next))
  },

  setSettingsAdvancedMode: (advanced) => {
    set({ settingsAdvancedMode: advanced })
    persistBool('settingsAdvancedMode', advanced)
  },

  markSettingsAdvancedWarned: () => {
    if (get().settingsAdvancedWarned) return
    set({ settingsAdvancedWarned: true })
    persistBool('settingsAdvancedWarned', true)
  },

  acknowledgeTunnelIntro: () => {
    set({ tunnelIntroAcknowledged: true })
    persistBool('tunnelIntroAcknowledged', true)
  },

  dismissPostSetupGuidance: () => {
    set({ postSetupGuidanceDismissed: true })
    persistBool('postSetupGuidanceDismissed', true)
  },

  hydrate: async (): Promise<void> => {
    try {
      const entries = await getNamespaceSettings('dashboard')
      const byKey = new Map(entries.map((e) => [e.definition.key, e.value]))
      const patch: Partial<DashboardPrefs> = {}
      const recents = byKey.get(BACKEND_KEY.commandRecents)
      if (recents !== undefined) patch.commandRecents = parseRecents(recents)
      patch.sidebarCollapsed = byKey.get(BACKEND_KEY.sidebarCollapsed) === 'true'
      patch.settingsAdvancedMode = byKey.get(BACKEND_KEY.settingsAdvancedMode) === 'true'
      patch.settingsAdvancedWarned = byKey.get(BACKEND_KEY.settingsAdvancedWarned) === 'true'
      patch.tunnelIntroAcknowledged = byKey.get(BACKEND_KEY.tunnelIntroAcknowledged) === 'true'
      patch.postSetupGuidanceDismissed
        = byKey.get(BACKEND_KEY.postSetupGuidanceDismissed) === 'true'
      set({ ...patch, hydrated: true })
    } catch (err) {
      log.warn('Failed to hydrate dashboard prefs from backend, using defaults:', getErrorMessage(err))
      set({ hydrated: true })
    }
  },
}))

/**
 * Reset the singleton store to defaults. Backend-sourced (no client
 * persistence), so the only cross-test leak is in-memory state in a shared
 * Vitest worker; the global ``afterEach`` in ``test-setup.tsx`` calls this.
 */
export function resetDashboardPrefs(): void {
  useDashboardPrefs.setState({ ...DEFAULTS, hydrated: false })
}
