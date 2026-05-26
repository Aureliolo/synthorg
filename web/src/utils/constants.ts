/** Application-wide constants. */

import type { CeremonyStrategyType, VelocityCalcType } from '@/api/types/ceremony-policy'
import type { TaskStatus } from '@/api/types/enums'
import type { SettingNamespace } from '@/api/types/settings'

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
 * Max characters kept when sanitizing untrusted strings (server error
 * reasons, WS disconnect codes, etc.) for logging. Tighter than display
 * caps because log lines get truncated by aggregators and the control
 * chars / bidi overrides already get stripped by `sanitizeForLog`.
 */
export const LOG_SANITIZE_MAX_LENGTH = 200

export const HEALTH_POLL_INTERVAL = 15000

export const DEFAULT_PAGE_SIZE = 50
export const MAX_PAGE_SIZE = 200

export const MIN_PASSWORD_LENGTH = 12

export const LOGIN_MAX_ATTEMPTS = 5
export const LOGIN_LOCKOUT_MS = 60_000

/** Ordered task statuses for Kanban columns. */
export const TASK_STATUS_ORDER: readonly TaskStatus[] = [
  'created',
  'assigned',
  'in_progress',
  'auth_required',
  'in_review',
  'blocked',
  'completed',
  'failed',
  'interrupted',
  'suspended',
  'rejected',
  'cancelled',
] as const

/** Terminal task statuses that cannot transition further. */
export const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set<TaskStatus>([
  'completed',
  'cancelled',
  'rejected',
])

/** Task status transitions map. */
export const VALID_TRANSITIONS: Readonly<Record<TaskStatus, readonly TaskStatus[]>> = {
  created: ['assigned', 'rejected'],
  assigned: ['in_progress', 'auth_required', 'blocked', 'cancelled', 'failed', 'interrupted', 'suspended'],
  in_progress: ['in_review', 'auth_required', 'blocked', 'cancelled', 'failed', 'interrupted', 'suspended'],
  in_review: ['completed', 'in_progress', 'blocked', 'cancelled'],
  auth_required: ['assigned', 'cancelled'],
  blocked: ['assigned'],
  failed: ['assigned'],
  interrupted: ['assigned'],
  suspended: ['assigned'],
  completed: [],
  cancelled: [],
  rejected: [],
}

/** Write-capable human roles. */
export const WRITE_ROLES = ['ceo', 'manager', 'pair_programmer'] as const

// ── Settings ────────────────────────────────────────────────

/** localStorage key for the basic/advanced toggle state. */
export const SETTINGS_ADVANCED_KEY = 'settings_show_advanced'

/** Display order for setting namespaces shown in the Settings page.
 * Excluded:
 *   - 'company' and 'providers': have dedicated pages.
 *   - 'settings': service-managed internal knobs.
 * Every other namespace the backend registry exposes is surfaced
 * here. Each setting's `restart_required` flag is honoured by
 * RestartBadge. */
export const NAMESPACE_ORDER: readonly SettingNamespace[] = [
  'api',
  'memory',
  'budget',
  'security',
  'coordination',
  'objectives',
  'observability',
  'cockpit',
  'telemetry',
  'backup',
  'engine',
  'research',
  'communication',
  'a2a',
  'integrations',
  'meta',
  'charter',
  'notifications',
  'simulations',
  'tools',
  'external_api',
  'hr',
  'workers',
  'client',
] as const

/** Human-readable display names for setting namespaces. */
export const NAMESPACE_DISPLAY_NAMES: Readonly<Record<SettingNamespace, string>> = {
  api: 'Server',
  client: 'Client',
  company: 'Company',
  providers: 'Providers',
  memory: 'Memory',
  budget: 'Budget',
  security: 'Security',
  coordination: 'Coordination',
  observability: 'Observability',
  backup: 'Backup',
  engine: 'Engine',
  research: 'Research',
  communication: 'Communication',
  a2a: 'A2A Federation',
  integrations: 'Integrations',
  meta: 'Meta-Agent',
  charter: 'Charter',
  notifications: 'Notifications',
  objectives: 'Objectives',
  simulations: 'Simulations',
  tools: 'Tools',
  settings: 'Settings (internal)',
  hr: 'HR',
  workers: 'Workers',
  telemetry: 'Telemetry',
  external_api: 'External API',
  cockpit: 'Mission Control',
}

/** sessionStorage key for the advanced-mode first-toggle warning. */
export const SETTINGS_ADVANCED_WARNED_KEY = 'settings_advanced_warned'

/** Settings that should never be shown in the GUI (internal/system-managed). */
const HIDDEN_SETTING_KEYS = [
  'api/setup_complete',
  'observability/sink_overrides',
  'observability/custom_sinks',
] as const
export const HIDDEN_SETTINGS: ReadonlySet<string> = new Set(HIDDEN_SETTING_KEYS)

/**
 * Settings that carry elevated security risk when misconfigured.
 * The GUI shows an additional warning for these keys.
 */
const SECURITY_SENSITIVE_KEYS = ['api/auth_exclude_paths'] as const
export const SECURITY_SENSITIVE_SETTINGS: ReadonlySet<string> = new Set(SECURITY_SENSITIVE_KEYS)

/** Settings that are simple string arrays and should render as chip inputs in GUI mode. */
export const SIMPLE_ARRAY_SETTINGS: ReadonlySet<string> = new Set([
  'api/cors_allowed_origins',
  'api/rate_limit_exclude_paths',
  'api/auth_exclude_paths',
])

/**
 * Frontend-maintained setting dependency map.
 * Key: the "controller" setting (ns/key). Value: dependent settings (ns/key).
 * When the controller is disabled/false, dependents show a muted state.
 */
export const SETTING_DEPENDENCIES: Readonly<Record<string, readonly string[]>> = {
  'budget/auto_downgrade_enabled': ['budget/auto_downgrade_threshold'],
  'backup/enabled': ['backup/schedule_hours', 'backup/retention_days', 'backup/path'],
  'security/post_tool_scanning_enabled': ['security/output_scan_policy_type'],
}

/** Polling interval for settings page (ms). */
export const SETTINGS_POLL_INTERVAL = 60_000

// ── Ceremony Policy ─────────────────────────────────────────

export const CEREMONY_STRATEGY_LABELS: Readonly<Record<CeremonyStrategyType, string>> = {
  task_driven: 'Task Driven',
  calendar: 'Calendar',
  hybrid: 'Hybrid',
  event_driven: 'Event Driven',
  budget_driven: 'Budget Driven',
  throughput_adaptive: 'Throughput Adaptive',
  external_trigger: 'External Trigger',
  milestone_driven: 'Milestone Driven',
}

export const CEREMONY_STRATEGY_DESCRIPTIONS: Readonly<Record<CeremonyStrategyType, string>> = {
  task_driven: 'Ceremonies fire at task-count milestones. Natural fit for agent speed.',
  calendar: 'Traditional time-based scheduling using wall-clock cadence.',
  hybrid: 'Calendar + task-driven, whichever fires first wins.',
  event_driven: 'Ceremonies subscribe to engine events with configurable debounce.',
  budget_driven: 'Ceremonies fire at cost-consumption thresholds.',
  throughput_adaptive: 'Ceremonies fire when throughput rate changes significantly.',
  external_trigger: 'Ceremonies fire on external signals (webhooks, git events, MCP).',
  milestone_driven: 'Ceremonies fire at semantic project milestones.',
}

export const VELOCITY_CALC_LABELS: Readonly<Record<VelocityCalcType, string>> = {
  task_driven: 'Per Task (pts/task)',
  calendar: 'Per Day (pts/day)',
  multi_dimensional: 'Multi-Dimensional (pts/sprint)',
  budget: 'Per Currency Unit (pts/EUR)',
  points_per_sprint: 'Points per Sprint',
}

export const VELOCITY_UNIT_LABELS: Readonly<Record<VelocityCalcType, string>> = {
  task_driven: 'pts/task',
  calendar: 'pts/day',
  multi_dimensional: 'pts/sprint',
  budget: 'pts/EUR',
  points_per_sprint: 'pts/sprint',
}

export const STRATEGY_DEFAULT_VELOCITY_CALC: Readonly<Record<CeremonyStrategyType, VelocityCalcType>> = {
  task_driven: 'task_driven',
  calendar: 'calendar',
  hybrid: 'multi_dimensional',
  event_driven: 'points_per_sprint',
  budget_driven: 'budget',
  throughput_adaptive: 'task_driven',
  external_trigger: 'points_per_sprint',
  milestone_driven: 'points_per_sprint',
}

export const CEREMONY_STRATEGY_TYPES: readonly CeremonyStrategyType[] = [
  'task_driven',
  'calendar',
  'hybrid',
  'event_driven',
  'budget_driven',
  'throughput_adaptive',
  'external_trigger',
  'milestone_driven',
] as const

export const VELOCITY_CALC_TYPES: readonly VelocityCalcType[] = [
  'task_driven',
  'calendar',
  'multi_dimensional',
  'budget',
  'points_per_sprint',
] as const

export const WORKFLOW_TYPES = [
  'sequential_pipeline',
  'parallel_execution',
  'kanban',
  'agile_kanban',
] as const
