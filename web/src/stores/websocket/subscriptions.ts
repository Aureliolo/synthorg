import type {
  WsChannel,
  WsEvent,
  WsEventHandler,
  WsSubscriptionFilters,
} from '@/api/types/websocket'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCurrentSocket } from './transport-shared'
import type { WsGet } from './types'

const log = createLogger('ws')

/** Build a stable deduplication key for a subscription (sorted channels + sorted filter keys). */
function subscriptionKey(
  channels: WsChannel[],
  filters?: Record<string, string>,
): string {
  const sortedChannels = [...channels].sort()
  const sortedFilters: Record<string, string> = {}
  if (filters) {
    for (const [key, value] of Object.entries(filters).sort(([a], [b]) => a.localeCompare(b))) {
      sortedFilters[key] = value
    }
  }
  return JSON.stringify({ channels: sortedChannels, filters: sortedFilters })
}

/**
 * Upper bound on distinct active subscriptions. Normal operation dedupes by
 * key so the array never grows unbounded, but a bookkeeping regression (e.g.
 * a key that never matches on unsubscribe) would leak entries; crossing this
 * watermark logs a warning so the leak surfaces instead of silently growing.
 */
const MAX_ACTIVE_SUBSCRIPTIONS = 50

const channelHandlers = new Map<string, Set<WsEventHandler>>()
let pendingSubscriptions: {
  channels: WsChannel[]
  filters?: Record<string, string> | undefined
}[] = []
const activeSubscriptions: {
  channels: WsChannel[]
  filters?: Record<string, string> | undefined
}[] = []

/**
 * Fan an event out to per-channel handlers AND wildcard handlers.
 * Handler errors are isolated: one throwing subscriber cannot prevent
 * sibling subscribers from receiving the event.
 */
export function dispatchEvent(event: WsEvent): void {
  channelHandlers.get(event.channel)?.forEach((h) => {
    try {
      h(event)
    } catch (err) {
      log.error('Channel handler error:', err)
    }
  })
  channelHandlers.get('*')?.forEach((h) => {
    try {
      h(event)
    } catch (err) {
      log.error('Wildcard handler error:', err)
    }
  })
}

function queueSubscriptionForReconnect(
  channels: WsChannel[],
  filters: WsSubscriptionFilters | undefined,
  key: string,
): void {
  if (
    !pendingSubscriptions.some(
      (s) => subscriptionKey(s.channels, s.filters) === key,
    )
  ) {
    pendingSubscriptions.push({ channels, filters })
  }
}

/**
 * Send every active subscription on a freshly-opened socket. Called
 * from transport's ``_onOpen`` right after the auth frame. Wipes the
 * ``pendingSubscriptions`` queue first: any queued-while-disconnected
 * entries are necessarily a subset of ``activeSubscriptions`` (every
 * ``subscribe()`` push lands in both arrays), so iterating active
 * covers them. The order on the wire is auth -> subscribe(s); the
 * server's auth_ok ack can land before or after the subscribe ack and
 * both orderings are safe.
 */
export function replaySubscriptions(target: WebSocket): void {
  pendingSubscriptions = []
  for (const sub of activeSubscriptions) {
    try {
      target.send(
        JSON.stringify({
          action: 'subscribe',
          channels: sub.channels,
          filters: sub.filters,
        }),
      )
    } catch (err) {
      log.error('Subscribe send failed (will retry on reconnect):', err)
    }
  }
}

/**
 * Idempotent teardown: clears every subscription bookkeeping list and
 * the channel-handler map. Called from both the aggregator's
 * ``teardown()`` action (test reset) and the ``disconnect()`` action
 * (intentional user-driven shutdown).
 */
export function teardownSubscriptions(): void {
  pendingSubscriptions = []
  activeSubscriptions.length = 0
  channelHandlers.clear()
}

function subscribe(
  channels: WsChannel[],
  filters?: WsSubscriptionFilters,
): void {
  const key = subscriptionKey(channels, filters)
  if (
    !activeSubscriptions.some(
      (s) => subscriptionKey(s.channels, s.filters) === key,
    )
  ) {
    activeSubscriptions.push({
      channels: [...channels],
      filters: filters ? { ...filters } : undefined,
    })
    if (activeSubscriptions.length > MAX_ACTIVE_SUBSCRIPTIONS) {
      log.warn('Active WS subscriptions exceeded watermark', {
        count: activeSubscriptions.length,
        max: MAX_ACTIVE_SUBSCRIPTIONS,
      })
    }
  }

  const currentSocket = getCurrentSocket()
  if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
    if (
      !pendingSubscriptions.some(
        (s) => subscriptionKey(s.channels, s.filters) === key,
      )
    ) {
      pendingSubscriptions.push({ channels, filters })
    }
    return
  }
  const frame = JSON.stringify({ action: 'subscribe', channels, filters })
  try {
    currentSocket.send(frame)
  } catch (err) {
    // D1: a transient send failure (e.g. an instant of socket
    // back-pressure) used to drop straight into the reconnect
    // queue, stranding the subscription for tens of seconds until
    // the auth_ok handshake replayed it. Schedule one immediate
    // microtask retry against the same socket so a single
    // failure does not silently disable the channel; on the
    // second failure (or if the socket has moved out of OPEN),
    // fall through to the queue-for-reconnect path.
    log.warn(
      'Subscribe send failed, retrying on next microtask:',
      sanitizeForLog(err),
    )
    const retrySocket = currentSocket
    queueMicrotask(() => {
      if (retrySocket !== getCurrentSocket()) return
      // Re-check the subscription is still live: an ``unsubscribe`` in
      // the same tick could have removed ``key`` from
      // ``activeSubscriptions`` after the send failed but before this
      // microtask runs. Sending ``subscribe`` for a key the caller has
      // already torn down would re-attach the handler on the server
      // without a matching client-side handler entry.
      const stillActive = activeSubscriptions.some(
        (s) => subscriptionKey(s.channels, s.filters) === key,
      )
      if (!stillActive) return
      if (retrySocket.readyState !== WebSocket.OPEN) {
        queueSubscriptionForReconnect(channels, filters, key)
        return
      }
      try {
        retrySocket.send(frame)
      } catch (retryErr) {
        log.error(
          'Subscribe send retry failed, queued for reconnect:',
          sanitizeForLog(retryErr),
        )
        queueSubscriptionForReconnect(channels, filters, key)
      }
    })
  }
}

/** Drop the unsubscribed channels from each entry, splicing out emptied ones. */
function pruneSubscriptionList(
  list: { channels: WsChannel[]; filters?: Record<string, string> | undefined }[],
  channelSet: ReadonlySet<WsChannel>,
): void {
  for (let i = list.length - 1; i >= 0; i--) {
    const sub = list[i]
    if (!sub) continue
    sub.channels = sub.channels.filter((c) => !channelSet.has(c))
    if (sub.channels.length === 0) list.splice(i, 1)
  }
}

function unsubscribe(channels: WsChannel[]): void {
  const channelSet = new Set(channels)
  pruneSubscriptionList(activeSubscriptions, channelSet)
  pruneSubscriptionList(pendingSubscriptions, channelSet)

  const currentSocket = getCurrentSocket()
  if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) return
  try {
    currentSocket.send(JSON.stringify({ action: 'unsubscribe', channels }))
  } catch (err) {
    log.error('Unsubscribe send failed:', err)
  }
}

function onChannelEvent(
  channel: WsChannel | '*',
  handler: WsEventHandler,
): void {
  const handlers = channelHandlers.get(channel) ?? new Set()
  if (!channelHandlers.has(channel)) {
    channelHandlers.set(channel, handlers)
  }
  handlers.add(handler)
}

function offChannelEvent(
  channel: WsChannel | '*',
  handler: WsEventHandler,
): void {
  channelHandlers.get(channel)?.delete(handler)
}

export function createSubscriptionsSlice(get: WsGet) {
  // Route teardown through the store accessor so callers (tests) that
  // ``vi.spyOn(store, 'offChannelEvent' | 'unsubscribe')`` continue to
  // intercept the rollback path. Calling the module-local closures
  // directly would bypass the store's action surface and silently break
  // the spy contract every external consumer of ``rollbackSubscriptions``
  // depends on.
  function rollbackSubscriptions(
    channels: readonly WsChannel[],
    bindings: readonly { channel: WsChannel; handler: WsEventHandler }[],
    options?: { unsubscribe?: boolean },
  ): void {
    // Best-effort teardown. Each leg is independently safe:
    // ``offChannelEvent`` is a Map/Set delete (cannot throw) and
    // ``unsubscribe`` swallows its own send failures via ``log.error``.
    // A ``try``/``catch`` around each leg defends against future
    // store actions that may throw without forcing callers (the hook)
    // to own store error UX.
    const self = get()
    for (const binding of bindings) {
      try {
        self.offChannelEvent(binding.channel, binding.handler)
      } catch (err) {
        log.error('rollbackSubscriptions: offChannelEvent failed:', err)
      }
    }
    if (options?.unsubscribe !== false && channels.length > 0) {
      // Only unsubscribe channels that no longer have any handler
      // registrations. Multiple ``useWebSocket`` hooks can share a
      // channel, so an unmount that blindly unsubscribes every
      // channel in its own binding set would cut off broadcast
      // traffic for sibling hooks that are still mounted. Consult
      // the module-scope ``channelHandlers`` map after the per-
      // binding ``offChannelEvent`` calls above have pruned this
      // hook's own entries; any channel with a non-empty Set is
      // still in use by another subscriber.
      const channelsToUnsubscribe = [...new Set(channels)].filter(
        (channel) => (channelHandlers.get(channel)?.size ?? 0) === 0,
      )
      if (channelsToUnsubscribe.length > 0) {
        try {
          self.unsubscribe(channelsToUnsubscribe)
        } catch (err) {
          log.error('rollbackSubscriptions: unsubscribe failed:', err)
        }
      }
    }
  }

  return {
    subscribe,
    unsubscribe,
    onChannelEvent,
    offChannelEvent,
    rollbackSubscriptions,
  }
}
