/** Settings registry, sink configuration and parsed company-config entries. */

import type { AutonomyLevel, SeniorityLevel } from './enums'
import type { DepartmentReportingLine } from './org'

export type SettingNamespace =
  | 'api'
  | 'client'
  | 'company'
  | 'providers'
  | 'memory'
  | 'budget'
  | 'security'
  | 'coordination'
  | 'observability'
  | 'backup'
  | 'engine'
  | 'communication'
  | 'a2a'
  | 'integrations'
  | 'meta'
  | 'notifications'
  | 'simulations'
  | 'tools'
  | 'settings'
  | 'hr'
  | 'workers'
  | 'telemetry'

export type SettingType = 'str' | 'int' | 'float' | 'bool' | 'enum' | 'json'

export type SettingLevel = 'basic' | 'advanced'

export type SettingSource = 'db' | 'env' | 'default'

export interface SettingDefinition {
  namespace: SettingNamespace
  key: string
  type: SettingType
  default: string | null
  description: string
  group: string
  level: SettingLevel
  sensitive: boolean
  restart_required: boolean
  /**
   * Whether the value is resolved only from env / YAML at startup and
   * cannot be mutated through ``/settings`` afterwards. Implies
   * ``restart_required``. The dashboard disables the input for these
   * fields and surfaces a notice directing operators to configure them
   * via environment variable or YAML before launch.
   */
  read_only_post_init: boolean
  /**
   * Override for the auto-derived ``SYNTHORG_{NAMESPACE}_{KEY}`` env
   * var name (used when an established operator-facing env var name
   * predates the auto-derivation rule, e.g. ``SYNTHORG_LOG_DIR``).
   */
  env_var_override: string | null
  enum_values: readonly string[]
  validator_pattern: string | null
  min_value: number | null
  max_value: number | null
  yaml_path: string | null
}

export interface SettingEntry {
  definition: SettingDefinition
  value: string
  source: SettingSource
  updated_at: string | null
}

/** Backend enforces max_length=65536 on value. */
export interface UpdateSettingRequest {
  value: string
}

export interface SinkRotation {
  strategy: 'builtin' | 'external'
  max_bytes: number
  backup_count: number
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface SinkInfo {
  identifier: string
  sink_type: 'console' | 'file'
  level: LogLevel
  json_format: boolean
  rotation: SinkRotation | null
  is_default: boolean
  enabled: boolean
  routing_prefixes: readonly string[]
}

export interface TestSinkResult {
  valid: boolean
  error: string | null
}

export interface AgentConfigEntry {
  name: string
  role: string
  department: string
  level: SeniorityLevel
  personality?: Record<string, unknown>
  model?: Record<string, unknown>
  memory?: Record<string, unknown>
  tools?: Record<string, unknown>
  authority?: Record<string, unknown>
  autonomy_level?: AutonomyLevel | null
}

export interface DepartmentTeam {
  readonly name: string
  readonly lead?: string
  readonly members?: readonly string[]
}

export interface DepartmentEntry {
  readonly name: string
  readonly head?: string
  readonly head_id?: string | null
  readonly budget_percent?: number
  readonly teams?: readonly DepartmentTeam[]
  readonly reporting_lines?: readonly DepartmentReportingLine[]
  readonly autonomy_level?: AutonomyLevel | null
  readonly policies?: Record<string, unknown>
}
