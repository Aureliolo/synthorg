import {
  createConnection as apiCreateConnection,
  deleteConnection as apiDeleteConnection,
  updateConnection as apiUpdateConnection,
} from '@/api/endpoints/connections'
import type {
  Connection,
  CreateConnectionRequest,
  UpdateConnectionRequest,
} from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import type { ConnectionsGet, ConnectionsSet } from './types'

const log = createLogger('connections-crud')

export function createCrudActions(set: ConnectionsSet, get: ConnectionsGet) {
  return {
    createConnection: async (
      data: CreateConnectionRequest,
    ): Promise<Connection | null> => {
      set({ mutating: true })
      try {
        const created = await apiCreateConnection(data)
        const state = get()
        set({
          connections: [...state.connections, created],
          mutating: false,
        })
        useToastStore.getState().add({
          variant: 'success',
          title: `Connection ${created.name} created`,
        })
        return created
      } catch (err) {
        log.error('Create connection failed:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to create connection'),
          description: getErrorMessage(err),
        })
        set({ mutating: false })
        return null
      }
    },

    updateConnection: async (
      name: string,
      data: UpdateConnectionRequest,
    ): Promise<Connection | null> => {
      set({ mutating: true })
      try {
        const updated = await apiUpdateConnection(name, data)
        const state = get()
        set({
          connections: state.connections.map((c) =>
            c.name === name ? updated : c,
          ),
          mutating: false,
        })
        useToastStore.getState().add({
          variant: 'success',
          title: `Connection ${name} updated`,
        })
        return updated
      } catch (err) {
        log.error('Update connection failed:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to update connection'),
          description: getErrorMessage(err),
        })
        set({ mutating: false })
        return null
      }
    },

    deleteConnection: async (name: string): Promise<boolean> => {
      const previous = get().connections
      set({
        mutating: true,
        connections: previous.filter((c) => c.name !== name),
      })
      try {
        await apiDeleteConnection(name)
        set({ mutating: false })
        useToastStore.getState().add({
          variant: 'success',
          title: `Connection ${name} deleted`,
        })
        return true
      } catch (err) {
        log.error('Delete connection failed:', getErrorMessage(err))
        set({ mutating: false, connections: previous })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to delete connection'),
          description: getErrorMessage(err),
        })
        return false
      }
    },
  }
}
