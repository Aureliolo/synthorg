import { create } from 'zustand'
import { getNamespaceSettings, resetSetting, updateSetting } from '@/api/endpoints/settings'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('theme')

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ColorPalette = 'warm-ops' | 'ice-station' | 'stealth' | 'signal' | 'neon'
export type Density = 'dense' | 'balanced' | 'medium' | 'sparse'
export type Typography = 'geist' | 'jetbrains' | 'ibm-plex'
export type AnimationPreset = 'minimal' | 'spring' | 'instant' | 'status-driven' | 'aggressive'
export type SidebarMode = 'rail' | 'collapsible' | 'hidden' | 'persistent' | 'compact'

export interface ThemePreferences {
  colorPalette: ColorPalette
  density: Density
  typography: Typography
  animation: AnimationPreset
  sidebarMode: SidebarMode
}

export interface ThemeState extends ThemePreferences {
  popoverOpen: boolean
  reducedMotionDetected: boolean
  /** True once preferences have been hydrated from the backend at least once. */
  hydrated: boolean
  setColorPalette: (value: ColorPalette) => void
  setDensity: (value: Density) => void
  setTypography: (value: Typography) => void
  setAnimation: (value: AnimationPreset) => void
  setSidebarMode: (value: SidebarMode) => void
  setPopoverOpen: (open: boolean) => void
  /**
   * Load appearance preferences from the backend (`appearance` settings
   * namespace) and apply them. The dashboard is a pure API consumer: the
   * backend is the source of truth and there is no client-side copy, so this
   * runs once the authed shell mounts. Failures degrade to defaults.
   */
  hydrate: () => Promise<void>
  reset: () => void
  /**
   * Detach the matchMedia listener installed at store creation.
   * Called from the global afterEach in test-setup.tsx so the
   * active-handle gate does not fail the test on a forgotten
   * listener. Also invoked from Vite's `import.meta.hot` dispose
   * hook to avoid leaking listeners across Fast Refresh cycles in
   * dev. Idempotent.
   */
  teardown: () => void
  /**
   * Re-attach the `prefers-reduced-motion` matchMedia listener after
   * a prior `teardown()`. Idempotent: calling it while the listener
   * is already attached is a no-op. Needed because the store is a
   * Zustand singleton whose closure refs are permanently nulled by
   * `teardown()`; without this method, tests running after the
   * global `afterEach` have a store that no longer reacts to OS
   * reduced-motion preference changes. Tests that exercise runtime
   * reduced-motion reactivity should call `reattach()` after
   * mocking `window.matchMedia`.
   */
  reattach: () => void
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const COLOR_PALETTES = ['warm-ops', 'ice-station', 'stealth', 'signal', 'neon'] as const satisfies readonly ColorPalette[]
export const DENSITIES = ['dense', 'balanced', 'medium', 'sparse'] as const satisfies readonly Density[]
export const TYPOGRAPHIES = ['geist', 'jetbrains', 'ibm-plex'] as const satisfies readonly Typography[]
export const ANIMATION_PRESETS = ['minimal', 'spring', 'instant', 'status-driven', 'aggressive'] as const satisfies readonly AnimationPreset[]
export const SIDEBAR_MODES = ['rail', 'collapsible', 'hidden', 'persistent', 'compact'] as const satisfies readonly SidebarMode[]

// Map each preference axis to its backend `appearance.*` settings key. The
// frontend uses camelCase; the backend namespace uses snake_case.
const BACKEND_KEY = {
  colorPalette: 'color_palette',
  density: 'density',
  typography: 'typography',
  animation: 'animation',
  sidebarMode: 'sidebar_mode',
} as const satisfies Record<keyof ThemePreferences, string>

const FRONTEND_KEY: Record<string, keyof ThemePreferences> = {
  color_palette: 'colorPalette',
  density: 'density',
  typography: 'typography',
  animation: 'animation',
  sidebar_mode: 'sidebarMode',
}

// CSS classes for each axis (applied to <html>)
const THEME_CLASSES = COLOR_PALETTES.map((p) => `theme-${p}`)
const DENSITY_CLASSES = DENSITIES.filter((d) => d !== 'balanced').map((d) => `density-${d}`)
const TYPOGRAPHY_CLASSES = TYPOGRAPHIES.filter((t) => t !== 'geist').map((t) => `typography-${t}`)
const ANIMATION_CLASSES = ANIMATION_PRESETS.map((a) => `animation-${a}`)
const SIDEBAR_CLASSES = SIDEBAR_MODES.filter((s) => s !== 'collapsible').map((s) => `sidebar-${s}`)

const ALL_THEME_CLASSES = [
  ...THEME_CLASSES,
  ...DENSITY_CLASSES,
  ...TYPOGRAPHY_CLASSES,
  ...ANIMATION_CLASSES,
  ...SIDEBAR_CLASSES,
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function detectReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function getDefaultPreferences(): ThemePreferences {
  return {
    colorPalette: 'warm-ops',
    density: 'balanced',
    typography: 'geist',
    animation: detectReducedMotion() ? 'minimal' : 'status-driven',
    sidebarMode: 'collapsible',
  }
}

function isValid<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
}

function mergeStoredPrefs(
  obj: Record<string, unknown>,
  defaults: ThemePreferences,
): ThemePreferences {
  return {
    colorPalette: isValid(obj['colorPalette'], COLOR_PALETTES)
      ? obj['colorPalette']
      : defaults.colorPalette,
    density: isValid(obj['density'], DENSITIES)
      ? obj['density']
      : defaults.density,
    typography: isValid(obj['typography'], TYPOGRAPHIES)
      ? obj['typography']
      : defaults.typography,
    animation: isValid(obj['animation'], ANIMATION_PRESETS)
      ? obj['animation']
      : defaults.animation,
    sidebarMode: isValid(obj['sidebarMode'], SIDEBAR_MODES)
      ? obj['sidebarMode']
      : defaults.sidebarMode,
  }
}

/** Guard against CSS class name injection -- only lowercase alphanumeric and hyphens. */
const CSS_CLASS_SAFE = /^[a-z0-9-]+$/

function safeClass(cls: string): string {
  if (!CSS_CLASS_SAFE.test(cls)) {
    throw new Error(`Unsafe CSS class name blocked (length=${cls.length})`)
  }
  return cls
}

/** Apply theme classes to document.documentElement. */
export function applyThemeClasses(prefs: ThemePreferences): void {
  if (typeof document === 'undefined') return
  const el = document.documentElement

  // Remove all existing theme classes
  el.classList.remove(...ALL_THEME_CLASSES)

  // Add current classes (skip defaults that have no class)
  if (prefs.colorPalette !== 'warm-ops') {
    el.classList.add(safeClass(`theme-${prefs.colorPalette}`))
  }
  if (prefs.density !== 'balanced') {
    el.classList.add(safeClass(`density-${prefs.density}`))
  }
  if (prefs.typography !== 'geist') {
    el.classList.add(safeClass(`typography-${prefs.typography}`))
  }
  el.classList.add(safeClass(`animation-${prefs.animation}`))
  if (prefs.sidebarMode !== 'collapsible') {
    el.classList.add(safeClass(`sidebar-${prefs.sidebarMode}`))
  }
}

function getPrefs(state: ThemeState): ThemePreferences {
  return {
    colorPalette: state.colorPalette,
    density: state.density,
    typography: state.typography,
    animation: state.animation,
    sidebarMode: state.sidebarMode,
  }
}

/** Persist a single appearance preference to the backend; toast on failure. */
async function persistPref(key: keyof ThemePreferences, value: string): Promise<void> {
  try {
    await updateSetting('appearance', BACKEND_KEY[key], { value })
  } catch (err) {
    log.error('Failed to save appearance preference:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to save appearance'),
      description: getErrorMessage(err),
    })
  }
}

function applyPrefPatch(
  set: (partial: Partial<ThemeState>) => void,
  get: () => ThemeState,
  patch: Partial<ThemePreferences>,
): void {
  set(patch)
  const prefs = { ...getPrefs(get()), ...patch }
  applyThemeClasses(prefs)
  for (const [key, value] of Object.entries(patch)) {
    void persistPref(key as keyof ThemePreferences, value)
  }
}

/** Hydrate preferences from the backend appearance namespace; degrade to defaults. */
async function hydrateAppearance(set: (partial: Partial<ThemeState>) => void): Promise<void> {
  try {
    const entries = await getNamespaceSettings('appearance')
    const obj: Record<string, unknown> = {}
    for (const entry of entries) {
      const frontendKey = FRONTEND_KEY[entry.definition.key]
      if (frontendKey !== undefined) obj[frontendKey] = entry.value
    }
    const prefs = mergeStoredPrefs(obj, getDefaultPreferences())
    applyThemeClasses(prefs)
    set({ ...prefs, hydrated: true })
  } catch (err) {
    // Degrade to defaults; a logged-out shell (the settings GET is authed) or
    // a transient failure must not break paint. Reapply the default classes
    // here rather than relying on the construction-time apply still being on
    // the DOM, so a failed (re)hydrate never leaves the UI unthemed.
    log.warn('Failed to hydrate appearance from backend, using defaults:', getErrorMessage(err))
    applyThemeClasses(getDefaultPreferences())
    set({ ...getDefaultPreferences(), hydrated: true })
  }
}

/** Restore default preferences and clear the backend appearance overrides. */
function resetAppearance(set: (partial: Partial<ThemeState>) => void): void {
  const defaults = getDefaultPreferences()
  set({ ...defaults })
  applyThemeClasses(defaults)
  for (const backendKey of Object.values(BACKEND_KEY)) {
    void resetSetting('appearance', backendKey).catch((err: unknown) => {
      log.warn('Failed to reset appearance preference:', getErrorMessage(err))
    })
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useThemeStore = create<ThemeState>()((set, get) => {
  // Start from defaults and apply them synchronously so the shell is styled
  // before the backend hydrate lands. The backend is the source of truth;
  // ``hydrate()`` (called once the authed shell mounts) overlays the operator's
  // stored choices. The default palette adds no class, so a default install
  // shows no flash.
  const initial = getDefaultPreferences()
  const reducedMotion = detectReducedMotion()

  try {
    applyThemeClasses(initial)
  } catch (err) {
    log.warn('Failed to apply initial theme classes:', err)
  }

  // Listen for reduced-motion changes. Capture both the MediaQueryList
  // and the change handler in closure-scoped refs so `teardown()` can
  // call `removeEventListener` with the same handler identity. Set to
  // `null` after teardown so a second call is a no-op -- and so
  // `reattach()` can re-install a fresh pair without duplicate adds.
  let mql: MediaQueryList | null = null
  let reducedMotionHandler: ((e: MediaQueryListEvent) => void) | null = null

  // Install the listener against the current `window.matchMedia`.
  // Factored out so both the initial store creation AND `reattach()`
  // drive the same code path. Idempotent: a second call while the
  // listener is still attached is a no-op, so per-test
  // reattach/teardown remains symmetric under the active-handle gate.
  //
  // The OS reduced-motion preference is a RUNTIME presentation override: it
  // applies classes + updates state but does NOT write to the backend, so one
  // client's OS setting never clobbers the operator's stored animation choice.
  const attachReducedMotionListener = (): void => {
    if (mql && reducedMotionHandler) return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotionHandler = (e) => {
      set({ reducedMotionDetected: e.matches })
      const state = get()
      const defaults = getDefaultPreferences()
      if (state.animation === defaults.animation || (e.matches && state.animation !== 'minimal')) {
        const newAnimation: AnimationPreset = e.matches ? 'minimal' : 'status-driven'
        applyThemeClasses({ ...getPrefs(state), animation: newAnimation })
        set({ animation: newAnimation })
      }
    }
    mql.addEventListener('change', reducedMotionHandler)
  }

  attachReducedMotionListener()

  return {
    ...initial,
    popoverOpen: false,
    reducedMotionDetected: reducedMotion,
    hydrated: false,

    setColorPalette: (colorPalette) => {
      applyPrefPatch(set, get, { colorPalette })
    },
    setDensity: (density) => {
      applyPrefPatch(set, get, { density })
    },
    setTypography: (typography) => {
      applyPrefPatch(set, get, { typography })
    },
    setAnimation: (animation) => {
      applyPrefPatch(set, get, { animation })
    },
    setSidebarMode: (sidebarMode) => {
      applyPrefPatch(set, get, { sidebarMode })
    },

    setPopoverOpen: (popoverOpen) => set({ popoverOpen }),

    hydrate: () => hydrateAppearance(set),

    reset: () => {
      resetAppearance(set)
    },

    teardown: (): void => {
      if (mql && reducedMotionHandler) {
        mql.removeEventListener('change', reducedMotionHandler)
      }
      mql = null
      reducedMotionHandler = null
    },

    reattach: (): void => {
      // Capture the pre-attach state so we only replay the handler
      // when ``reattach()`` is actually re-installing a fresh
      // listener (after a prior ``teardown()``). Calling
      // ``reattach()`` on an already-attached store must be a
      // no-op -- otherwise repeated calls would drive the handler
      // on every invocation, causing avoidable DOM churn.
      const wasDetached = !(mql && reducedMotionHandler)
      attachReducedMotionListener()
      if (wasDetached && mql && reducedMotionHandler) {
        reducedMotionHandler({ matches: mql.matches } as MediaQueryListEvent)
      }
    },
  }
})

// Dev-only: release the matchMedia listener across Vite Fast Refresh
// so we do not layer duplicate handlers in the dev loop. In production
// Vite dead-code-eliminates this branch; under any non-Vite bundler
// `import.meta.hot` is `undefined` and the `typeof` guard skips the
// call safely.
if (typeof import.meta.hot !== 'undefined') {
  import.meta.hot.dispose(() => {
    useThemeStore.getState().teardown()
  })
}
