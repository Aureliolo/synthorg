/**
 * Notifications store -- unified notification pipeline.
 *
 * WS events and frontend events flow through `enqueue()`, which
 * fans out to toast, drawer (store items), and browser Notification
 * API based on category routing config and user preferences.
 */

import { create } from 'zustand'
import { createEnqueueAction } from './notifications/enqueue-actions'
import { createListActions } from './notifications/list-actions'
import {
  cancelPendingPersist,
  countUnread,
  hydrateItems,
  hydratePrefs,
  setNextIdFromHydrated,
} from './notifications/persistence'
import { createPreferenceActions } from './notifications/preference-actions'
import { createWsHandler } from './notifications/ws-handler'
import type { NotificationsState } from './notifications/types'

export type { EnqueueParams, NotificationsState } from './notifications/types'
export { cancelPendingPersist }

export const useNotificationsStore = create<NotificationsState>()(
  (set, get) => {
    const initialItems = hydrateItems()
    const initialPrefs = hydratePrefs()
    setNextIdFromHydrated(initialItems)
    return {
      items: initialItems,
      unreadCount: countUnread(initialItems),
      preferences: initialPrefs,

      ...createEnqueueAction(set, get),
      ...createListActions(set, get),
      ...createPreferenceActions(set, get),
      ...createWsHandler(get),
    }
  },
)
