import { create } from 'zustand'
import { createCrudActions } from './connections/crud-actions'
import { createListActions } from './connections/list-actions'
import type { ConnectionsState } from './connections/types'

export type { ConnectionsState } from './connections/types'

const INITIAL_STATE = {
  connections: [] as const,
  healthMap: {},
  listLoading: false,
  listError: null,
  connectionTypes: [] as const,
  typesLoading: false,
  typesError: null,
  searchQuery: '',
  typeFilter: null,
  healthFilter: null,
  sortBy: 'name' as const,
  sortDirection: 'asc' as const,
  checkingHealth: [] as const,
  mutating: false,
}

export const useConnectionsStore = create<ConnectionsState>()((set, get) => ({
  ...INITIAL_STATE,
  ...createListActions(set, get),
  ...createCrudActions(set, get),
  reset: () => set({ ...INITIAL_STATE }),
}))
