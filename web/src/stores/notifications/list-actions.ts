import { countUnread, debouncedPersist } from './persistence'
import type { NotificationsGet, NotificationsSet } from './types'

export function createListActions(
  set: NotificationsSet,
  get: NotificationsGet,
) {
  return {
    markRead(id: string): void {
      set((state) => {
        const items = state.items.map((item) =>
          item.id === id ? { ...item, read: true } : item,
        )
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    markAllRead(): void {
      set((state) => {
        const items = state.items.map((item) => ({ ...item, read: true }))
        return { items, unreadCount: 0 }
      })
      debouncedPersist(get())
    },

    dismiss(id: string): void {
      set((state) => {
        const items = state.items.filter((item) => item.id !== id)
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    markReadBatch(ids: readonly string[]): void {
      set((state) => {
        const idSet = new Set(ids)
        const updated = state.items.map((item) =>
          idSet.has(item.id) ? { ...item, read: true } : item,
        )
        return { items: updated, unreadCount: countUnread(updated) }
      })
      debouncedPersist(get())
    },

    dismissBatch(ids: readonly string[]): void {
      set((state) => {
        const idSet = new Set(ids)
        const items = state.items.filter((item) => !idSet.has(item.id))
        return { items, unreadCount: countUnread(items) }
      })
      debouncedPersist(get())
    },

    clearAll(): void {
      set({ items: [], unreadCount: 0 })
      debouncedPersist(get())
    },
  }
}
