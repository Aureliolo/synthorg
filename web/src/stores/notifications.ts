/**
 * Notifications store -- unified notification pipeline.
 *
 * WS events and frontend events flow through `enqueue()`, which
 * fans out to toast, drawer (store items), and browser Notification
 * API based on category routing config and user preferences.
 *
 * The dashboard is a pure API consumer: drawer items are an ephemeral
 * session buffer of the live WS stream (never persisted client-side; the
 * Activity feed is the durable history), and routing preferences live in the
 * backend ``notifications`` settings namespace, hydrated via `hydrate()`.
 */

import { create } from 'zustand'
import { DEFAULT_PREFERENCES } from '@/types/notifications'
import { createEnqueueAction } from './notifications/enqueue-actions'
import { createListActions } from './notifications/list-actions'
import {
  countUnread,
  hydrateItems,
  hydratePreferences,
  setNextIdFromHydrated,
} from './notifications/persistence'
import { createPreferenceActions } from './notifications/preference-actions'
import { createWsHandler } from './notifications/ws-handler'
import type { NotificationsState } from './notifications/types'

export type { EnqueueParams, NotificationsState } from './notifications/types'

export const useNotificationsStore = create<NotificationsState>()(
  (set, get) => {
    const initialItems = hydrateItems()
    setNextIdFromHydrated(initialItems)
    return {
      items: initialItems,
      unreadCount: countUnread(initialItems),
      preferences: DEFAULT_PREFERENCES,

      hydrate: async (): Promise<void> => {
        set({ preferences: await hydratePreferences() })
      },

      ...createEnqueueAction(set, get),
      ...createListActions(set),
      ...createPreferenceActions(set, get),
      ...createWsHandler(get),
    }
  },
)
