/**
 * Application-wide constants.
 *
 * Domain-specific constant clusters live with their owners:
 *   - WebSocket / SSE transport: `@/utils/ws-constants`
 *   - Task status ordering + transitions: `@/utils/tasks`
 *   - Ceremony / velocity display: `@/stores/ceremony-policy-constants`
 *   - Settings-page structure: `@/pages/settings/settings-constants`
 *   - Workflow creation: `@/pages/workflows/workflow-constants`
 */

export const HEALTH_POLL_INTERVAL = 15000

export const DEFAULT_PAGE_SIZE = 50
export const MAX_PAGE_SIZE = 200

export const MIN_PASSWORD_LENGTH = 12

export const LOGIN_MAX_ATTEMPTS = 5
export const LOGIN_LOCKOUT_MS = 60_000

/** Polling interval for settings page (ms). */
export const SETTINGS_POLL_INTERVAL = 60_000

/**
 * Polling interval (ms) for the interrupts fallback. Only active while
 * the live WebSocket transport is down, so a tighter cadence than the
 * settings poll is acceptable: pending interrupts block agents and the
 * operator needs them surfaced promptly.
 */
export const INTERRUPTS_POLL_INTERVAL = 10_000
