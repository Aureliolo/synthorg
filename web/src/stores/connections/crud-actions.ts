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

function emitCrudError(
  err: unknown,
  fallbackTitle: string,
  logPrefix: string,
): void {
  log.error(`${logPrefix}:`, getErrorMessage(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

async function createConnectionImpl(
  set: ConnectionsSet,
  get: ConnectionsGet,
  data: CreateConnectionRequest,
): Promise<Connection | null> {
  set({ mutating: true })
  try {
    const created = await apiCreateConnection(data)
    const state = get()
    set({ connections: [...state.connections, created], mutating: false })
    useToastStore.getState().add({
      variant: 'success',
      title: `Connection ${created.name} created`,
    })
    return created
  } catch (err) {
    set({ mutating: false })
    emitCrudError(err, 'Failed to create connection', 'Create connection failed')
    return null
  }
}

async function updateConnectionImpl(
  set: ConnectionsSet,
  get: ConnectionsGet,
  name: string,
  data: UpdateConnectionRequest,
): Promise<Connection | null> {
  set({ mutating: true })
  try {
    const updated = await apiUpdateConnection(name, data)
    const state = get()
    set({
      connections: state.connections.map((c) => (c.name === name ? updated : c)),
      mutating: false,
    })
    useToastStore.getState().add({
      variant: 'success',
      title: `Connection ${name} updated`,
    })
    return updated
  } catch (err) {
    set({ mutating: false })
    emitCrudError(err, 'Failed to update connection', 'Update connection failed')
    return null
  }
}

async function deleteConnectionImpl(
  set: ConnectionsSet,
  get: ConnectionsGet,
  name: string,
): Promise<boolean> {
  // Capture only the specific row we optimistically remove. Restoring
  // the full ``previous`` snapshot on failure could clobber connections
  // legitimately added or updated by a concurrent request between the
  // optimistic remove and the failure.
  const removed = get().connections.find((c) => c.name === name) ?? null
  set((s) => ({
    mutating: true,
    connections: s.connections.filter((c) => c.name !== name),
  }))
  try {
    await apiDeleteConnection(name)
    set({ mutating: false })
    useToastStore.getState().add({
      variant: 'success',
      title: `Connection ${name} deleted`,
    })
    return true
  } catch (err) {
    set((s) => {
      const alreadyBack = s.connections.some((c) => c.name === name)
      const restored = !alreadyBack && removed !== null
        ? [removed, ...s.connections]
        : s.connections
      return { mutating: false, connections: restored }
    })
    emitCrudError(err, 'Failed to delete connection', 'Delete connection failed')
    return false
  }
}

export function createCrudActions(set: ConnectionsSet, get: ConnectionsGet) {
  return {
    createConnection: (data: CreateConnectionRequest) =>
      createConnectionImpl(set, get, data),
    updateConnection: (name: string, data: UpdateConnectionRequest) =>
      updateConnectionImpl(set, get, name, data),
    deleteConnection: (name: string) =>
      deleteConnectionImpl(set, get, name),
  }
}
