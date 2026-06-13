/**
 * Notification types and routing configuration.
 *
 * The frontend defines fine-grained subcategories (e.g. `approvals.pending`,
 * `budget.exhausted`) for UI routing. The backend defines coarse categories
 * (`approval`, `budget`, etc.) for sink-level routing in
 * `src/synthorg/notifications/models.py`. The `NotificationSeverity` type
 * is shared 1:1 between backend and frontend.
 */

import type { ToastVariant } from '@/stores/toast'

// ---------------------------------------------------------------------------
// Core types
// ---------------------------------------------------------------------------

export type NotificationSeverity = 'info' | 'warning' | 'error' | 'critical'

export type NotificationCategory =
  | 'approvals.pending'
  | 'approvals.expiring'
  | 'approvals.decided'
  | 'escalations.pending'
  | 'escalations.decided'
  | 'escalations.expired'
  | 'escalations.cancelled'
  | 'budget.threshold'
  | 'budget.exhausted'
  | 'system.error'
  | 'system.restart_required'
  | 'system.shutdown'
  | 'agents.personality_trimmed'
  | 'agents.hired'
  | 'agents.fired'
  | 'tasks.failed'
  | 'tasks.blocked'
  | 'providers.down'
  | 'providers.degraded'
  | 'connection.lost'
  | 'connection.exhausted'

export type NotificationRoute = 'toast' | 'drawer' | 'browser'

export type NotificationFilterGroup =
  | 'all'
  | 'approvals'
  | 'escalations'
  | 'budget'
  | 'system'
  | 'tasks'
  | 'agents'
  | 'providers'
  | 'connection'

export interface NotificationItem {
  readonly id: string
  readonly category: NotificationCategory
  readonly severity: NotificationSeverity
  readonly title: string
  readonly description?: string | undefined
  readonly timestamp: string
  readonly read: boolean
  readonly href?: string | undefined
  readonly entityId?: string | undefined
  readonly dispatchedTo: readonly NotificationRoute[]
}

// ---------------------------------------------------------------------------
// Routing configuration
// ---------------------------------------------------------------------------

export interface CategoryConfig {
  readonly severity: NotificationSeverity
  readonly defaultRoutes: readonly NotificationRoute[]
  readonly label: string
  readonly group: NotificationFilterGroup
}

export const CATEGORY_CONFIGS: Record<NotificationCategory, CategoryConfig> = {
  'approvals.pending': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Approval pending',
    group: 'approvals',
  },
  'approvals.expiring': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'browser'],
    label: 'Approval expiring',
    group: 'approvals',
  },
  'approvals.decided': {
    severity: 'info',
    defaultRoutes: ['drawer'],
    label: 'Approval decided',
    group: 'approvals',
  },
  'escalations.pending': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Escalation pending',
    group: 'escalations',
  },
  'escalations.decided': {
    severity: 'info',
    defaultRoutes: ['drawer'],
    label: 'Escalation decided',
    group: 'escalations',
  },
  'escalations.expired': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'browser'],
    label: 'Escalation expired',
    group: 'escalations',
  },
  'escalations.cancelled': {
    severity: 'info',
    defaultRoutes: ['drawer'],
    label: 'Escalation cancelled',
    group: 'escalations',
  },
  'budget.threshold': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Budget threshold',
    group: 'budget',
  },
  'budget.exhausted': {
    severity: 'critical',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Budget exhausted',
    group: 'budget',
  },
  'system.error': {
    severity: 'error',
    defaultRoutes: ['drawer', 'toast'],
    label: 'System error',
    group: 'system',
  },
  'system.restart_required': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'toast'],
    label: 'Restart required',
    group: 'system',
  },
  'system.shutdown': {
    severity: 'critical',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'System shutdown',
    group: 'system',
  },
  'agents.personality_trimmed': {
    severity: 'info',
    defaultRoutes: ['toast'],
    label: 'Personality trimmed',
    group: 'agents',
  },
  'agents.hired': {
    severity: 'info',
    defaultRoutes: ['drawer'],
    label: 'Agent hired',
    group: 'agents',
  },
  'agents.fired': {
    severity: 'info',
    defaultRoutes: ['drawer'],
    label: 'Agent fired',
    group: 'agents',
  },
  'tasks.failed': {
    severity: 'error',
    defaultRoutes: ['drawer', 'toast'],
    label: 'Task failed',
    group: 'tasks',
  },
  'tasks.blocked': {
    severity: 'warning',
    defaultRoutes: ['drawer'],
    label: 'Task blocked',
    group: 'tasks',
  },
  // `connection.*` categories are driven by the frontend itself:
  // `useGlobalNotifications` enqueues `connection.lost` on WS setup
  // failure and `connection.exhausted` on reconnect budget exhaustion.
  // `providers.*` categories are catalogued here but not yet emitted
  // by the backend -- the provider health tracker has transition data
  // but no WebSocket notification sink ships today.  See
  // `useGlobalNotifications.ts` GLOBAL_CHANNELS for the live
  // subscription list and the backlog note tracking the provider
  // health -> WS notification bridge.
  'providers.down': {
    severity: 'error',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Provider down',
    group: 'providers',
  },
  'providers.degraded': {
    severity: 'warning',
    defaultRoutes: ['drawer', 'toast'],
    label: 'Provider degraded',
    group: 'providers',
  },
  'connection.lost': {
    severity: 'warning',
    defaultRoutes: ['toast'],
    label: 'Connection lost',
    group: 'connection',
  },
  'connection.exhausted': {
    severity: 'error',
    defaultRoutes: ['drawer', 'toast', 'browser'],
    label: 'Connection exhausted',
    group: 'connection',
  },
} as const

// ---------------------------------------------------------------------------
// Severity mapping to toast variant
// ---------------------------------------------------------------------------

export type { ToastVariant }

export const SEVERITY_TO_TOAST_VARIANT: Record<NotificationSeverity, ToastVariant> = {
  info: 'info',
  warning: 'warning',
  error: 'error',
  critical: 'error',
}

// ---------------------------------------------------------------------------
// Preferences
// ---------------------------------------------------------------------------

export interface NotificationPreferences {
  readonly routeOverrides: Partial<Record<NotificationCategory, readonly NotificationRoute[]>>
  readonly globalMute: boolean
  readonly browserPermission: NotificationPermission
}

export const DEFAULT_PREFERENCES: NotificationPreferences = {
  routeOverrides: {},
  globalMute: false,
  browserPermission: 'default',
}

// ---------------------------------------------------------------------------
// Filter group labels
// ---------------------------------------------------------------------------

export const FILTER_GROUP_LABELS: Record<NotificationFilterGroup, string> = {
  all: 'All',
  approvals: 'Approvals',
  escalations: 'Escalations',
  budget: 'Budget',
  system: 'System',
  tasks: 'Tasks',
  agents: 'Agents',
  providers: 'Providers',
  connection: 'Connection',
}
