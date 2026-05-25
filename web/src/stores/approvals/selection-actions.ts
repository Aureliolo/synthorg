import type { ApprovalsSet } from './types'

export function createSelectionActions(set: ApprovalsSet) {
  return {
    toggleSelection(id: string) {
      set((s) => {
        const next = new Set(s.selectedIds)
        if (next.has(id)) {
          next.delete(id)
        } else {
          next.add(id)
        }
        return { selectedIds: next }
      })
    },

    selectAllInGroup(ids: string[]) {
      set((s) => {
        const next = new Set(s.selectedIds)
        for (const id of ids) next.add(id)
        return { selectedIds: next }
      })
    },

    deselectAllInGroup(ids: string[]) {
      set((s) => {
        const next = new Set(s.selectedIds)
        for (const id of ids) next.delete(id)
        return { selectedIds: next }
      })
    },

    clearSelection() {
      set({ selectedIds: new Set() })
    },
  }
}
