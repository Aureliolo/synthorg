/** First-run setup wizard DTOs. */

export type {
  AvailableLocalesResponse,
  DiscoverModelsResponse,
  PersonalityPresetInfoResponse as PersonalityPresetInfo,
  SetupAgentRequest,
  SetupAgentResponse,
  SetupAgentSummary,
  SetupCompanyRequest,
  SetupCompanyResponse,
  SetupNameLocalesRequest,
  SetupNameLocalesResponse,
  SetupStatusResponse,
  TemplateInfoResponse,
  TemplateVariableResponse as TemplateVariable,
  UpdateAgentModelRequest,
  UpdateAgentNameRequest,
  UpdateAgentPersonalityRequest,
} from './dtos.gen'

export type { SkillPattern } from './enum-values.gen'
export { SKILL_PATTERN_VALUES } from './enum-values.gen'

/** Endpoint-only request body (OpenAPI uses an inline body schema
 *  rather than a named component). */
export interface DiscoverModelsRequest {
  preset_hint?: string
}
