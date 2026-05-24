import { create } from 'zustand'
import {
  createSubscriptionsSlice,
  teardownSubscriptions,
} from './websocket/subscriptions'
import {
  createTransportSlice,
  teardownTransport,
} from './websocket/transport'
import type { WebSocketState } from './websocket/types'

export type { WebSocketState } from './websocket/types'

export const useWebSocketStore = create<WebSocketState>()((set, get) => ({
  connected: false,
  reconnectExhausted: false,
  subscribedChannels: [],

  ...createTransportSlice(set, get),
  ...createSubscriptionsSlice(),

  teardown() {
    teardownTransport()
    teardownSubscriptions()
    set({
      connected: false,
      reconnectExhausted: false,
      subscribedChannels: [],
    })
  },
}))

// Vite Fast Refresh dispose hook: tear down sockets, timers, and event
// handlers when this module is replaced in dev. Without this, an HMR
// swap leaves the previous module's interval / reconnect chain armed
// against the previous socket, causing duplicate writers and ghost
// reconnects in the dev server. Mirrors the theme store's
// ``import.meta.hot?.dispose(...)`` per ``web/CLAUDE.md`` ("Test
// teardown").
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    useWebSocketStore.getState().teardown()
  })
}
