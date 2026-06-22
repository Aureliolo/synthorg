/** Settings-page display + GUI-form-structure constants. */

import type { SettingNamespace } from '@/api/types/settings'

/** localStorage key for the basic/advanced toggle state. */
export const SETTINGS_ADVANCED_KEY = 'settings_show_advanced'

/** sessionStorage key for the advanced-mode first-toggle warning. */
export const SETTINGS_ADVANCED_WARNED_KEY = 'settings_advanced_warned'

/** Display order for setting namespaces shown in the Settings page.
 * Excluded:
 *   - 'providers': the dedicated Providers page surfaces every
 *     registry key for that namespace.
 *   - 'settings': service-managed internal knobs.
 *   - 'demo': synthetic discovery-regression guard, not a real
 *     product feature, so it stays out of the user-facing UI.
 * 'company' IS surfaced here: the Org Edit page only covers the
 * company REST API (name / autonomy / budget), so without the generic
 * panel the registry-only keys (description, name_locales) would be
 * unreachable. The structural JSON blobs (company/agents,
 * company/departments) are managed via the dedicated tabs and hidden
 * from the panel (see HIDDEN_SETTINGS).
 * Every other namespace the backend registry exposes is surfaced
 * here. Each setting's `restart_required` flag is honoured by
 * RestartBadge. */
export const NAMESPACE_ORDER: readonly SettingNamespace[] = [
  'api',
  'company',
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
  demo: 'Demo',
}

/** Settings that should never be shown in the GUI (internal/system-managed). */
const HIDDEN_SETTING_KEYS = [
  'api/setup_complete',
  'observability/sink_overrides',
  'observability/custom_sinks',
  // Large structural JSON blobs managed via the dedicated Org Edit tabs;
  // hidden from the generic panel to keep it readable (the panel still
  // surfaces company/description + company/name_locales).
  'company/agents',
  'company/departments',
] as const
export const HIDDEN_SETTINGS: ReadonlySet<string> = new Set(HIDDEN_SETTING_KEYS)

/**
 * Settings hidden from the YAML code editor. Narrower than
 * {@link HIDDEN_SETTINGS}: the complex observability sink settings are
 * hidden from the GUI form (they have a dedicated sinks UI) but ARE
 * editable as raw YAML in the code editor, so only the truly
 * system-managed flag stays out of both surfaces.
 */
const CODE_EDITOR_HIDDEN_SETTING_KEYS = ['api/setup_complete'] as const
export const CODE_EDITOR_HIDDEN_SETTINGS: ReadonlySet<string> = new Set(
  CODE_EDITOR_HIDDEN_SETTING_KEYS,
)

/**
 * Placeholder substituted for ``sensitive`` setting values in the code
 * editor so secrets never render in the YAML buffer. Saving this exact
 * value back is rejected (treated as "unchanged") so the real secret is
 * never overwritten with the placeholder.
 */
export const SENSITIVE_VALUE_PLACEHOLDER = '••••••••'

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
