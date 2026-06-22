/**
 * WebSocket + SSE transport constants.
 *
 * The wire-protocol subset (`WS_PROTOCOL_VERSION`, `WS_MAX_MESSAGE_SIZE`,
 * `WS_HEARTBEAT_INTERVAL_MS`, `WS_PONG_TIMEOUT_MS`, `LOG_SANITIZE_MAX_LENGTH`)
 * is the client-server contract and MUST stay in lockstep with
 * `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`.
 * Drift in `WS_PROTOCOL_VERSION` is enforced by
 * `scripts/check_ws_protocol_version_in_sync.py`.
 */

export const WS_RECONNECT_BASE_DELAY = 1000
export const WS_RECONNECT_MAX_DELAY = 30000
export const WS_MAX_RECONNECT_ATTEMPTS = 20
/**
 * +/-20% randomised jitter applied to the reconnect backoff so a
 * server-restart-driven reconnect storm does not arrive in lockstep
 * across every connected client.
 *
 * On wake-up, ``scheduleReconnect`` multiplies the deterministic
 * exponential delay by ``WS_RECONNECT_JITTER_MIN..MAX`` (uniform).
 * Lower bound 0.8 prevents the cap from saturating; upper bound 1.2
 * keeps reconnect latency tight enough to feel snappy.
 */
export const WS_RECONNECT_JITTER_MIN = 0.8
export const WS_RECONNECT_JITTER_MAX = 1.2
/**
 * Max incoming WS event size (bytes). Mirrors the server's
 * `_MAX_OUTBOUND_EVENT_BYTES` in `src/synthorg/api/controllers/ws.py`.
 * The 4 KiB cap on outbound (client → server) control messages is
 * enforced server-side and is intentionally tighter than this inbound cap.
 */
export const WS_MAX_MESSAGE_SIZE = 32_768
/** Heartbeat interval. 20s sits comfortably under the typical 60s proxy idle close. */
export const WS_HEARTBEAT_INTERVAL_MS = 20_000
/**
 * +/-5% randomised jitter applied to the heartbeat interval so a
 * fleet of long-lived dashboards does not ping the server in lockstep.
 * Each tick samples a uniform delay in
 * `WS_HEARTBEAT_INTERVAL_MS * [WS_HEARTBEAT_JITTER_MIN, WS_HEARTBEAT_JITTER_MAX]`,
 * matching the reconnect-backoff jitter pattern but with a tighter
 * band (5%) because heartbeat timing has stricter pong-deadline
 * coupling than reconnect.
 */
export const WS_HEARTBEAT_JITTER_MIN = 0.95
export const WS_HEARTBEAT_JITTER_MAX = 1.05
/** Max wait for a pong reply before treating the socket as dead and reconnecting. */
export const WS_PONG_TIMEOUT_MS = 10_000
/**
 * Wire-protocol version that this client understands. Events whose
 * `version` is absent are treated as `1`. Events whose `version` differs
 * are logged + discarded so a future server roll-out can ship breaking
 * changes without crashing older clients. Mirrors `WsEvent.version` in
 * `src/synthorg/api/ws_models.py`.
 */
export const WS_PROTOCOL_VERSION = 1

/**
 * Consecutive SSE-fallback transport errors tolerated before the client gives
 * up and surfaces an exhausted state. The browser's `EventSource` retries
 * indefinitely on its own, so without this budget a prolonged SSE outage floods
 * the backend with reconnect traffic; the WS path enforces the analogous
 * `WS_MAX_RECONNECT_ATTEMPTS`.
 */
export const SSE_MAX_RECONNECT_ATTEMPTS = 10

/**
 * Application-level exponential-backoff bounds (ms) for the SSE fallback. The
 * browser's native `EventSource` reconnect is a flat cadence with no backoff,
 * so a prolonged outage hammers the backend; the client instead closes the
 * source on error and re-opens after `min(BASE * 2**attempt, MAX)`, mirroring
 * the WS `WS_RECONNECT_BASE_DELAY` / `WS_RECONNECT_MAX_DELAY` pair.
 */
export const SSE_RECONNECT_BASE_DELAY = 1000
export const SSE_RECONNECT_MAX_DELAY = 30000

/**
 * Consecutive WS wire-version mismatches tolerated before the client flags a
 * persistent protocol mismatch (a server roll-out bumped the protocol and this
 * client can no longer decode events). A single mismatch where the received
 * version is newer than supported also trips it immediately.
 */
export const WS_PROTOCOL_MISMATCH_THRESHOLD = 5

/**
 * Window after a WebSocket-driven update during which a scheduled REST poll
 * skips its fetch. Shorter than the 30s poll interval so a sluggish or dropped
 * WS still results in eventual freshness via REST; long enough that a burst of
 * WS events does not also trigger a redundant poll. Shared by every
 * `usePolling` consumer that also subscribes to a WS channel.
 */
export const FRESHNESS_WINDOW_MS = 15_000

/**
 * Max characters kept when sanitizing untrusted strings (server error
 * reasons, WS disconnect codes, etc.) for logging. Tighter than display
 * caps because log lines get truncated by aggregators and the control
 * chars / bidi overrides already get stripped by `sanitizeForLog`.
 */
export const LOG_SANITIZE_MAX_LENGTH = 200
