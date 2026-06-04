import { create } from 'zustand'
import { createLogger } from '@/lib/logger'
import { asObjectRecord } from '@/utils/parse'

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
  setColorPalette: (value: ColorPalette) => void
  setDensity: (value: Density) => void
  setTypography: (value: Typography) => void
  setAnimation: (value: AnimationPreset) => void
  setSidebarMode: (value: SidebarMode) => void
  setPopoverOpen: (open: boolean) => void
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

const STORAGE_KEY = 'so_theme_preferences'

export const COLOR_PALETTES = ['warm-ops', 'ice-station', 'stealth', 'signal', 'neon'] as const satisfies readonly ColorPalette[]
export const DENSITIES = ['dense', 'balanced', 'medium', 'sparse'] as const satisfies readonly Density[]
export const TYPOGRAPHIES = ['geist', 'jetbrains', 'ibm-plex'] as const satisfies readonly Typography[]
export const ANIMATION_PRESETS = ['minimal', 'spring', 'instant', 'status-driven', 'aggressive'] as const satisfies readonly AnimationPreset[]
export const SIDEBAR_MODES = ['rail', 'collapsible', 'hidden', 'persistent', 'compact'] as const satisfies readonly SidebarMode[]

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

function getDefaultPreferences(): ThemePreferences {
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
    colorPalette: isValid(obj.colorPalette, COLOR_PALETTES)
      ? obj.colorPalette
      : defaults.colorPalette,
    density: isValid(obj.density, DENSITIES)
      ? obj.density
      : defaults.density,
    typography: isValid(obj.typography, TYPOGRAPHIES)
      ? obj.typography
      : defaults.typography,
    animation: isValid(obj.animation, ANIMATION_PRESETS)
      ? obj.animation
      : defaults.animation,
    sidebarMode: isValid(obj.sidebarMode, SIDEBAR_MODES)
      ? obj.sidebarMode
      : defaults.sidebarMode,
  }
}

/** Exported for testing only -- the store already calls this at creation time. */
export function loadPreferences(): ThemePreferences {
  const defaults = getDefaultPreferences()
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaults
    const parsed: unknown = JSON.parse(raw)
    const obj = asObjectRecord(parsed)
    if (!obj) return defaults
    return mergeStoredPrefs(obj, defaults)
  } catch (err) {
    log.warn('Failed to load preferences, using defaults:', err)
    return defaults
  }
}

function savePreferences(prefs: ThemePreferences): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch (err) {
    log.warn('Failed to save preferences (localStorage may be unavailable):', err)
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

function applyPrefPatch(
  set: (partial: Partial<ThemeState>) => void,
  get: () => ThemeState,
  patch: Partial<ThemePreferences>,
): void {
  set(patch)
  const prefs = { ...getPrefs(get()), ...patch }
  savePreferences(prefs)
  applyThemeClasses(prefs)
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useThemeStore = create<ThemeState>()((set, get) => {
  const initial = loadPreferences()
  const reducedMotion = detectReducedMotion()

  // Apply initial theme classes synchronously (wrapped for resilience;
  // a corrupted localStorage value that bypasses isValid would crash the app)
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
  // reattach/teardown remains symmetric under the active-handle
  // gate.
  //
  // The handler CANNOT be invoked synchronously here because this
  // function runs inside the Zustand creator before the store's
  // initial state is returned -- ``get()`` would see partial /
  // undefined fields and could write bogus preferences. Initial
  // alignment to ``mql.matches`` happens via the ``reducedMotion``
  // variable threaded into the store's returned object; a synthetic
  // replay is only safe once the store is fully constructed, which
  // is why ``reattach()`` (not the initial attach) is responsible
  // for firing the handler.
  const attachReducedMotionListener = (): void => {
    if (mql && reducedMotionHandler) return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotionHandler = (e) => {
      set({ reducedMotionDetected: e.matches })
      // Follow OS reduced-motion preference: always switch to minimal when
      // OS requests it; revert to default when OS lifts the preference
      // (unless user already chose minimal).
      const state = get()
      const defaults = getDefaultPreferences()
      if (state.animation === defaults.animation || (e.matches && state.animation !== 'minimal')) {
        const newAnimation: AnimationPreset = e.matches ? 'minimal' : 'status-driven'
        const prefs = { ...getPrefs(state), animation: newAnimation }
        savePreferences(prefs)
        applyThemeClasses(prefs)
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

    reset: () => {
      const defaults = getDefaultPreferences()
      set({ ...defaults })
      applyThemeClasses(defaults)
      try {
        localStorage.removeItem(STORAGE_KEY)
      } catch (err) {
        log.warn('Failed to clear stored preferences:', err)
      }
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
      // on every invocation, causing avoidable ``savePreferences``
      // localStorage writes and ``applyThemeClasses`` DOM churn.
      const wasDetached = !(mql && reducedMotionHandler)
      attachReducedMotionListener()
      // Safe to invoke the handler synchronously here: the store is
      // fully constructed by the time ``reattach()`` is callable, so
      // ``get()`` inside the handler returns complete state. The
      // replay mirrors today's OS preference (or the test's mocked
      // ``matchMedia`` value) into the store immediately, rather
      // than waiting for the next ``change`` event that may never
      // fire in tests. Initial store construction skips this replay
      // (see ``attachReducedMotionListener``).
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
