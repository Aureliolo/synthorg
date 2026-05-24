import type {
  NotificationCategory,
  NotificationRoute,
} from '@/types/notifications'
import { debouncedPersist } from './persistence'
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
      debouncedPersist(get())
    },

    resetRouteOverride(category: NotificationCategory): void {
      set((state) => {
        const { [category]: _removed, ...rest } = state.preferences.routeOverrides
        void _removed
        return {
          preferences: { ...state.preferences, routeOverrides: rest },
        }
      })
      debouncedPersist(get())
    },

    setGlobalMute(muted: boolean): void {
      set((state) => ({
        preferences: { ...state.preferences, globalMute: muted },
      }))
      debouncedPersist(get())
    },

    setBrowserPermission(perm: NotificationPermission): void {
      set((state) => ({
        preferences: { ...state.preferences, browserPermission: perm },
      }))
      debouncedPersist(get())
    },
  }
}
