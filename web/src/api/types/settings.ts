/** Settings registry, sink configuration and parsed company-config entries. */

import type { AutonomyLevel, SeniorityLevel } from './enums'
import type { DepartmentReportingLine } from './org'

export type {
  SettingDefinition,
  SettingEntry,
  SinkInfoResponse as SinkInfo,
  SinkRotationResponse as SinkRotation,
  TestSinkConfigResponse as TestSinkResult,
  UpdateSettingRequest,
} from './dtos.gen'

export type {
  SettingLevel,
  SettingNamespace,
  SettingSource,
  SettingType,
} from './enum-values.gen'
export {
  SETTING_LEVEL_VALUES,
  SETTING_NAMESPACE_VALUES,
  SETTING_SOURCE_VALUES,
  SETTING_TYPE_VALUES,
} from './enum-values.gen'

/** Frontend-only log level union (inline string union on the wire
 *  via SinkInfo.level; not a named OpenAPI schema). */
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

/** Parsed company-config entries surface as ``dict`` payloads on the
 *  wire; Pydantic does not expose them as ``components.schemas`` so
 *  the dashboard types remain hand-maintained. */
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
