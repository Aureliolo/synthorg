import type {
  NotificationCategory,
  NotificationRoute,
} from '@/types/notifications'
import { persistPreferences } from './persistence'
import type { NotificationsGet, NotificationsSet } from './types'

export function createPreferenceActions(
  set: NotificationsSet,
  get: NotificationsGet,
) {
  return {
    setRouteOverride(
      category: NotificationCategory,
      routes: readonly NotificationRoute[],
    ): void {
      set((state) => ({
        preferences: {
          ...state.preferences,
          routeOverrides: {
            ...state.preferences.routeOverrides,
            [category]: routes,
          },
        },
      }))
      void persistPreferences(get().preferences)
    },

    resetRouteOverride(category: NotificationCategory): void {
      set((state) => {
        const { [category]: _removed, ...rest } = state.preferences.routeOverrides
        void _removed
        return {
          preferences: { ...state.preferences, routeOverrides: rest },
        }
      })
      void persistPreferences(get().preferences)
    },

    setGlobalMute(muted: boolean): void {
      set((state) => ({
        preferences: { ...state.preferences, globalMute: muted },
      }))
      void persistPreferences(get().preferences)
    },

    setBrowserPermission(perm: NotificationPermission): void {
      // The browser Notification permission is per-device runtime state mirrored
      // from the browser API; it is NOT persisted to the backend preferences.
      set((state) => ({
        preferences: { ...state.preferences, browserPermission: perm },
      }))
    },
  }
}
