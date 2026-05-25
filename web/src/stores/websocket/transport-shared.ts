/**
 * Cross-slice accessors for the websocket package.
 *
 * The transport slice owns the live ``WebSocket`` reference, but the
 * subscriptions slice needs to send frames on it. Co-locating an
 * accessor pair in a separate module instead of a direct cross-slice
 * import keeps the dependency one-way (subscriptions -> shared,
 * transport -> shared) and avoids a circular module-load between the
 * two slice files.
 */

let currentSocket: WebSocket | null = null

export function getCurrentSocket(): WebSocket | null {
  return currentSocket
}

export function setCurrentSocket(next: WebSocket | null): void {
  currentSocket = next
}
