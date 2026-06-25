import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import {
  useThemeStore,
  applyThemeClasses,
  getDefaultPreferences,
} from '@/stores/theme'
import { server } from '@/test-setup'
import { successFor } from '@/mocks/handlers/helpers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import type { getNamespaceSettings } from '@/api/endpoints/settings'

function resetThemeState(): void {
  // The store is a singleton; restore the production defaults from their single
  // source of truth (``getDefaultPreferences``) so the fallback-path tests
  // verify the real defaults and new preference fields can't drift out of sync.
  useThemeStore.setState({
    ...getDefaultPreferences(),
    popoverOpen: false,
    hydrated: false,
  })
}

describe('useThemeStore', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    resetThemeState()
  })

  it('has correct default values', () => {
    const state = useThemeStore.getState()
    expect(state.colorPalette).toBe('warm-ops')
    expect(state.density).toBe('balanced')
    expect(state.typography).toBe('geist')
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

  describe('hydrate (backend source of truth)', () => {
    it('applies preferences fetched from the appearance namespace', async () => {
      server.use(
        http.get('/api/v1/settings/appearance', () =>
          HttpResponse.json(
            successFor<typeof getNamespaceSettings>([
              buildSettingEntry({
                value: 'neon',
                source: 'db',
                definition: { namespace: 'appearance', key: 'color_palette' },
              }),
              buildSettingEntry({
                value: 'dense',
                source: 'db',
                definition: { namespace: 'appearance', key: 'density' },
              }),
            ]),
          ),
        ),
      )

      await useThemeStore.getState().hydrate()

      const state = useThemeStore.getState()
      expect(state.colorPalette).toBe('neon')
      expect(state.density).toBe('dense')
      expect(state.hydrated).toBe(true)
      expect(document.documentElement.classList.contains('theme-neon')).toBe(true)
      expect(document.documentElement.classList.contains('density-dense')).toBe(true)
    })

    it('ignores unknown / invalid backend values and keeps defaults', async () => {
      server.use(
        http.get('/api/v1/settings/appearance', () =>
          HttpResponse.json(
            successFor<typeof getNamespaceSettings>([
              buildSettingEntry({
                value: 'not-a-palette',
                source: 'db',
                definition: { namespace: 'appearance', key: 'color_palette' },
              }),
            ]),
          ),
        ),
      )

      await useThemeStore.getState().hydrate()

      expect(useThemeStore.getState().colorPalette).toBe('warm-ops')
      expect(useThemeStore.getState().hydrated).toBe(true)
      // ``beforeEach`` clears the class list, and ``applyThemeClasses`` omits a
      // class for default-valued axes (warm-ops / balanced) but always sets the
      // animation class -- so this proves hydrate reapplied the default classes
      // rather than leaving the UI unthemed.
      expect(
        document.documentElement.classList.contains('animation-status-driven'),
      ).toBe(true)
    })

    it('degrades to defaults and still marks hydrated on fetch failure', async () => {
      server.use(
        http.get('/api/v1/settings/appearance', () =>
          HttpResponse.json({ detail: 'unauthorized' }, { status: 401 }),
        ),
      )

      await useThemeStore.getState().hydrate()

      expect(useThemeStore.getState().colorPalette).toBe('warm-ops')
      expect(useThemeStore.getState().hydrated).toBe(true)
      // Proves the catch path reapplied the default classes (the always-set
      // animation class) rather than leaving the UI unthemed after a 401.
      expect(
        document.documentElement.classList.contains('animation-status-driven'),
      ).toBe(true)
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
  })
})
