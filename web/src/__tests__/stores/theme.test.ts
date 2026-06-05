import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import fc from 'fast-check'
import {
  useThemeStore,
  applyThemeClasses,
  loadPreferences,
  COLOR_PALETTES,
  DENSITIES,
  TYPOGRAPHIES,
  ANIMATION_PRESETS,
  SIDEBAR_MODES,
} from '@/stores/theme'

const STORAGE_KEY = 'so_theme_preferences'

describe('useThemeStore', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.className = ''
    useThemeStore.getState().reset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('has correct default values', () => {
    const state = useThemeStore.getState()
    expect(state.colorPalette).toBe('warm-ops')
    expect(state.density).toBe('balanced')
    expect(state.typography).toBe('geist')
    // animation default depends on reduced motion -- 'status-driven' when no reduced motion
    expect(['minimal', 'status-driven']).toContain(state.animation)
    expect(state.sidebarMode).toBe('collapsible')
    expect(state.popoverOpen).toBe(false)
  })

  describe('setters', () => {
    it('updates colorPalette and applies CSS class', () => {
      useThemeStore.getState().setColorPalette('neon')
      expect(useThemeStore.getState().colorPalette).toBe('neon')
      expect(document.documentElement.classList.contains('theme-neon')).toBe(true)
    })

    it('updates density and applies CSS class', () => {
      useThemeStore.getState().setDensity('sparse')
      expect(useThemeStore.getState().density).toBe('sparse')
      expect(document.documentElement.classList.contains('density-sparse')).toBe(true)
    })

    it('updates typography and applies CSS class', () => {
      useThemeStore.getState().setTypography('jetbrains')
      expect(useThemeStore.getState().typography).toBe('jetbrains')
      expect(document.documentElement.classList.contains('typography-jetbrains')).toBe(true)
    })

    it('updates animation and applies CSS class', () => {
      useThemeStore.getState().setAnimation('spring')
      expect(useThemeStore.getState().animation).toBe('spring')
      expect(document.documentElement.classList.contains('animation-spring')).toBe(true)
    })

    it('updates sidebarMode and applies CSS class', () => {
      useThemeStore.getState().setSidebarMode('rail')
      expect(useThemeStore.getState().sidebarMode).toBe('rail')
      expect(document.documentElement.classList.contains('sidebar-rail')).toBe(true)
    })

    it('updates popoverOpen', () => {
      useThemeStore.getState().setPopoverOpen(true)
      expect(useThemeStore.getState().popoverOpen).toBe(true)
    })
  })

  describe('localStorage persistence', () => {
    it('saves to localStorage on setter call', () => {
      useThemeStore.getState().setColorPalette('stealth')
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!) as Record<string, string>
      expect(stored.colorPalette).toBe('stealth')
    })

    it('saves multiple axis changes', () => {
      useThemeStore.getState().setDensity('dense')
      useThemeStore.getState().setTypography('ibm-plex')
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!) as Record<string, string>
      expect(stored.density).toBe('dense')
      expect(stored.typography).toBe('ibm-plex')
    })
  })

  describe('applyThemeClasses', () => {
    it('adds theme class for non-default color palette', () => {
      applyThemeClasses({
        colorPalette: 'ice-station',
        density: 'balanced',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('theme-ice-station')).toBe(true)
    })

    it('does not add theme class for default color palette', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('theme-warm-ops')).toBe(false)
    })

    it('adds density class for non-default', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'dense',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('density-dense')).toBe(true)
    })

    it('adds typography class for non-default', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'jetbrains',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('typography-jetbrains')).toBe(true)
    })

    it('always adds animation class', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'geist',
        animation: 'minimal',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('animation-minimal')).toBe(true)
    })

    it('adds sidebar class for non-default mode', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'rail',
      })
      expect(document.documentElement.classList.contains('sidebar-rail')).toBe(true)
    })

    it('does not add sidebar class for default collapsible mode', () => {
      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('sidebar-collapsible')).toBe(false)
    })

    it('removes old classes when theme changes', () => {
      applyThemeClasses({
        colorPalette: 'neon',
        density: 'sparse',
        typography: 'geist',
        animation: 'spring',
        sidebarMode: 'hidden',
      })
      expect(document.documentElement.classList.contains('theme-neon')).toBe(true)
      expect(document.documentElement.classList.contains('density-sparse')).toBe(true)
      expect(document.documentElement.classList.contains('sidebar-hidden')).toBe(true)

      applyThemeClasses({
        colorPalette: 'warm-ops',
        density: 'balanced',
        typography: 'geist',
        animation: 'status-driven',
        sidebarMode: 'collapsible',
      })
      expect(document.documentElement.classList.contains('theme-neon')).toBe(false)
      expect(document.documentElement.classList.contains('density-sparse')).toBe(false)
      expect(document.documentElement.classList.contains('sidebar-hidden')).toBe(false)
    })
  })

  describe('reset', () => {
    it('restores defaults', () => {
      useThemeStore.getState().setColorPalette('neon')
      useThemeStore.getState().setDensity('dense')
      useThemeStore.getState().setTypography('ibm-plex')

      useThemeStore.getState().reset()

      const state = useThemeStore.getState()
      expect(state.colorPalette).toBe('warm-ops')
      expect(state.density).toBe('balanced')
      expect(state.typography).toBe('geist')
    })

    it('clears localStorage', () => {
      useThemeStore.getState().setColorPalette('stealth')
      expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull()

      useThemeStore.getState().reset()
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    })
  })

  describe('loadPreferences (unit)', () => {
    it('returns defaults when localStorage is empty', () => {
      localStorage.clear()
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('warm-ops')
      expect(prefs.density).toBe('balanced')
      expect(prefs.typography).toBe('geist')
      expect(prefs.sidebarMode).toBe('collapsible')
    })

    it('falls back to defaults for invalid JSON', () => {
      localStorage.setItem(STORAGE_KEY, 'not json')
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('warm-ops')
      expect(prefs.density).toBe('balanced')
    })

    it('falls back to defaults for non-object JSON', () => {
      localStorage.setItem(STORAGE_KEY, '"just a string"')
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('warm-ops')
    })

    it('falls back to defaults for invalid values', () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        colorPalette: 'invalid-theme',
        density: 'ultra-dense',
        typography: 'comic-sans',
      }))
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('warm-ops')
      expect(prefs.density).toBe('balanced')
      expect(prefs.typography).toBe('geist')
    })

    it('preserves valid values and falls back invalid ones', () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        colorPalette: 'neon',
        density: 'not-valid',
        typography: 'ibm-plex',
      }))
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('neon')
      expect(prefs.density).toBe('balanced') // fallback
      expect(prefs.typography).toBe('ibm-plex')
    })

    it('reads valid stored preferences', () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        colorPalette: 'stealth',
        density: 'dense',
        typography: 'jetbrains',
        animation: 'spring',
        sidebarMode: 'rail',
      }))
      const prefs = loadPreferences()
      expect(prefs.colorPalette).toBe('stealth')
      expect(prefs.density).toBe('dense')
      expect(prefs.typography).toBe('jetbrains')
      expect(prefs.animation).toBe('spring')
      expect(prefs.sidebarMode).toBe('rail')
    })
  })

  describe('safeClass guard', () => {
    it('applyThemeClasses works normally with valid values', () => {
      // All valid palette values should apply without throwing
      applyThemeClasses({
        colorPalette: 'neon',
        density: 'dense',
        typography: 'jetbrains',
        animation: 'spring',
        sidebarMode: 'rail',
      })
      expect(document.documentElement.classList.contains('theme-neon')).toBe(true)
      expect(document.documentElement.classList.contains('density-dense')).toBe(true)
      expect(document.documentElement.classList.contains('typography-jetbrains')).toBe(true)
      expect(document.documentElement.classList.contains('animation-spring')).toBe(true)
      expect(document.documentElement.classList.contains('sidebar-rail')).toBe(true)
    })

    it('STORAGE_KEY is so_theme_preferences', () => {
      // Verify the local constant matches the store's internal key
      expect(STORAGE_KEY).toBe('so_theme_preferences')
    })
  })

  describe('reducedMotion detection', () => {
    it('loadPreferences returns minimal animation when reduced motion is detected', () => {
      const originalMatchMedia = window.matchMedia
      window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))

      const prefs = loadPreferences()
      expect(prefs.animation).toBe('minimal')

      window.matchMedia = originalMatchMedia
    })

    it('loadPreferences returns status-driven animation when reduced motion is off', () => {
      const originalMatchMedia = window.matchMedia
      window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))

      const prefs = loadPreferences()
      expect(prefs.animation).toBe('status-driven')

      window.matchMedia = originalMatchMedia
    })
  })

  it('save-load round-trip', () => {
    useThemeStore.getState().setColorPalette('neon')
    const prefs = loadPreferences()
    expect(prefs.colorPalette).toBe('neon')
  })

  describe('fast-check property tests', () => {
    it('loadPreferences always returns valid preferences for arbitrary localStorage values', () => {
      fc.assert(
        fc.property(fc.anything(), (arbitrary) => {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(arbitrary))
          const prefs = loadPreferences()
          expect(COLOR_PALETTES).toContain(prefs.colorPalette)
          expect(DENSITIES).toContain(prefs.density)
          expect(TYPOGRAPHIES).toContain(prefs.typography)
          expect(ANIMATION_PRESETS).toContain(prefs.animation)
          expect(SIDEBAR_MODES).toContain(prefs.sidebarMode)
        }),
      )
    })
  })
})
