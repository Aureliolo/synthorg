/** First-run setup wizard DTOs. */

import type { SeniorityLevel } from './enums'

export interface SetupStatusResponse {
  needs_admin: boolean
  needs_setup: boolean
  has_providers: boolean
  has_name_locales: boolean
  has_company: boolean
  has_agents: boolean
  min_password_length: number
}

export interface DiscoverModelsRequest {
  preset_hint?: string
}

export type SkillPattern =
  | 'tool_wrapper'
  | 'generator'
  | 'reviewer'
  | 'inversion'
  | 'pipeline'

export interface TemplateVariable {
  readonly name: string
  readonly description: string
  readonly var_type: string
  readonly default: string | number | boolean | null
  readonly required: boolean
}

export interface TemplateInfoResponse {
  name: string
  display_name: string
  description: string
  source: 'builtin' | 'user'
  tags: readonly string[]
  skill_patterns: readonly SkillPattern[]
  variables: readonly TemplateVariable[]
  agent_count: number
  department_count: number
  autonomy_level: string
  workflow: string
}

export interface SetupCompanyRequest {
  company_name: string
  description: string | null
  template_name: string | null
}

export interface SetupAgentRequest {
  name: string
  role: string
  level: SeniorityLevel
  personality_preset: string
  model_provider: string
  model_id: string
  department: string
  budget_limit_monthly: number | null
}

export interface SetupAgentSummary {
  name: string
  role: string
  department: string
  level: SeniorityLevel | null
  model_provider: string | null
  model_id: string | null
  tier: string
  personality_preset: string | null
}

export interface SetupCompanyResponse {
  company_name: string
  description: string | null
  template_applied: string | null
  department_count: number
  agent_count: number
  readonly agents: readonly SetupAgentSummary[]
}

export interface SetupAgentResponse {
  name: string
  role: string
  department: string
  model_provider: string
  model_id: string
}

export interface UpdateAgentModelRequest {
  model_provider: string
  model_id: string
}

export interface UpdateAgentNameRequest {
  name: string
}

export interface UpdateAgentPersonalityRequest {
  personality_preset: string
}

export interface PersonalityPresetInfo {
  readonly name: string
  readonly description: string
}

export interface SetupNameLocalesRequest {
  locales: string[]
}

export interface SetupNameLocalesResponse {
  readonly locales: readonly string[]
}

export interface AvailableLocalesResponse {
  readonly regions: Readonly<Record<string, readonly string[]>>
  readonly display_names: Readonly<Record<string, string>>
}
