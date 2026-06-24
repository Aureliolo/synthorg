import { countUnread } from './persistence'
import type { NotificationsSet } from './types'

// Drawer items are an ephemeral session buffer (never persisted client-side),
// so list mutations only update in-memory state; there is nothing to flush.
export function createListActions(set: NotificationsSet) {
  return {
    markRead(id: string): void {
      set((state) => {
        const items = state.items.map((item) =>
          item.id === id ? { ...item, read: true } : item,
        )
        return { items, unreadCount: countUnread(items) }
      })
    },

    markAllRead(): void {
      set((state) => {
        const items = state.items.map((item) => ({ ...item, read: true }))
        return { items, unreadCount: 0 }
      })
    },

    dismiss(id: string): void {
      set((state) => {
        const items = state.items.filter((item) => item.id !== id)
        return { items, unreadCount: countUnread(items) }
      })
    },

    markReadBatch(ids: readonly string[]): void {
      set((state) => {
        const idSet = new Set(ids)
        const updated = state.items.map((item) =>
          idSet.has(item.id) ? { ...item, read: true } : item,
        )
        return { items: updated, unreadCount: countUnread(updated) }
      })
    },

    dismissBatch(ids: readonly string[]): void {
      set((state) => {
        const idSet = new Set(ids)
        const items = state.items.filter((item) => !idSet.has(item.id))
        return { items, unreadCount: countUnread(items) }
      })
    },

    clearAll(): void {
      set({ items: [], unreadCount: 0 })
    },
  }
}
