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

// ``sanitizeWsString`` and ``MAX_STRING_LEN`` live in
// ``@/utils/ws-sanitize`` so benchmark + unit-test imports can pull
// them in without dragging this store's side effects (toast queue,
// persistence subscription, ``localStorage`` hydration) into the
// import graph. We re-export here so existing call sites that
// import from ``@/stores/notifications`` keep working unchanged.
export {
  MAX_WS_STRING_LEN as MAX_STRING_LEN,
  sanitizeWsEnum,
  sanitizeWsString,
} from '@/utils/ws-sanitize'

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
