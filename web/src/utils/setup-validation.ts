/** Per-step validation rules for the setup wizard. */

import type { ProviderConfig } from '@/api/types/providers'
import type { SetupAgentSummary, SetupCompanyResponse } from '@/api/types/setup'

export interface StepValidationResult {
  readonly valid: boolean
  readonly errors: readonly string[]
}

/**
 * Company-step validation result. In addition to the generic `valid` /
 * `errors` fields, exposes structured gates so callers can enable/disable
 * specific affordances (e.g. the Apply-Template button) without string-matching
 * against error messages.
 */
export interface CompanyStepValidationResult extends StepValidationResult {
  /** True when the template-apply request has succeeded (companyResponse present). */
  readonly templateApplied: boolean
  /** True when the non-template errors (name length, etc.) are empty. */
  readonly baseDetailsValid: boolean
}

const VALID: StepValidationResult = { valid: true, errors: [] }

function invalid(...errors: string[]): StepValidationResult {
  return { valid: false, errors }
}

export const COMPANY_TEMPLATE_GATE_ERROR = 'Apply the template to continue'

// ── Step 1: Template ─────────────────────────────────────────

interface TemplateStepInput {
  readonly selectedTemplate: string | null
}

export function validateTemplateStep(input: TemplateStepInput): StepValidationResult {
  if (!input.selectedTemplate) {
    return invalid('Please select a template')
  }
  return VALID
}

// ── Step 2: Company ──────────────────────────────────────────

interface CompanyStepInput {
  readonly companyName: string
  readonly companyDescription: string
  readonly companyResponse: SetupCompanyResponse | null
}

const MAX_COMPANY_NAME_LENGTH = 200
const MAX_DESCRIPTION_LENGTH = 1000

// `.length` counts UTF-16 code units, so a single emoji ("👨‍💻") would
// consume up to 8 units against the 200-char limit. Spread iterates by code
// point, which is the closest cheap approximation to user-visible characters
// without pulling in Intl.Segmenter for non-Latin scripts.
export function graphemeLength(s: string): number {
  return [...s].length
}

export function validateCompanyStep(input: CompanyStepInput): CompanyStepValidationResult {
  const errors: string[] = []
  const trimmedName = input.companyName.trim()

  if (!trimmedName) {
    errors.push('Company name is required')
  } else if (graphemeLength(trimmedName) > MAX_COMPANY_NAME_LENGTH) {
    errors.push(`Company name must be ${MAX_COMPANY_NAME_LENGTH} characters or less`)
  }

  if (graphemeLength(input.companyDescription.trim()) > MAX_DESCRIPTION_LENGTH) {
    errors.push(`Description must be ${MAX_DESCRIPTION_LENGTH} characters or less`)
  }

  const baseDetailsValid = errors.length === 0
  const templateApplied = input.companyResponse !== null

  if (!templateApplied) {
    errors.push(COMPANY_TEMPLATE_GATE_ERROR)
  }

  return {
    valid: errors.length === 0,
    errors,
    templateApplied,
    baseDetailsValid,
  }
}

// ── Step 3: Agents ───────────────────────────────────────────

interface AgentsStepInput {
  readonly agents: readonly SetupAgentSummary[]
}

export function validateAgentsStep(input: AgentsStepInput): StepValidationResult {
  const errors: string[] = []

  if (input.agents.length === 0) {
    errors.push('At least one agent is required')
    return { valid: false, errors }
  }

  for (const agent of input.agents) {
    if (!agent.model_provider || !agent.model_id) {
      errors.push(`Agent "${agent.name}" is missing a model assignment`)
    }
  }

  return errors.length > 0 ? { valid: false, errors } : VALID
}

// ── Step 4: Providers ────────────────────────────────────────

interface ProvidersStepInput {
  readonly providers: Readonly<Record<string, ProviderConfig>>
}

export function validateProvidersStep(input: ProvidersStepInput): StepValidationResult {
  const errors: string[] = []
  const providerNames = Object.keys(input.providers)

  if (providerNames.length === 0) {
    errors.push('At least one provider is required')
    return { valid: false, errors }
  }

  // Every configured provider must expose at least one model. A provider that
  // the user adds without successful model discovery is dead weight: the
  // wizard's downstream agent step needs at least one model to wire each
  // agent's ``model`` field to.
  for (const [name, provider] of Object.entries(input.providers)) {
    if (provider.models.length === 0) {
      errors.push(
        `Provider "${name}" has no models. Run model discovery or add a model manually before continuing.`,
      )
    }
  }

  return errors.length > 0 ? { valid: false, errors } : VALID
}

// ── Cross-step: agent ↔ provider/model resolution ────────────

export type UnresolvedAgentReason =
  | 'unassigned'
  | 'missing_provider'
  | 'missing_model'

export interface UnresolvedAgent {
  readonly index: number
  readonly name: string
  readonly provider: string | null
  readonly modelId: string | null
  readonly reason: UnresolvedAgentReason
}

/**
 * Find agents whose ``model_provider`` / ``model_id`` cannot be resolved
 * against the current providers map. The AgentsStep banner and the
 * agents-step completion gate share this single source of truth so the
 * wizard nav and the in-page warning cannot disagree.
 */
export function resolveAgentModels(
  agents: readonly SetupAgentSummary[],
  providers: Readonly<Record<string, ProviderConfig>>,
): readonly UnresolvedAgent[] {
  const out: UnresolvedAgent[] = []
  agents.forEach((agent, index) => {
    const provider = agent.model_provider
    const modelId = agent.model_id
    if (!provider || !modelId) {
      out.push({ index, name: agent.name, provider, modelId, reason: 'unassigned' })
      return
    }
    const providerConfig = providers[provider]
    if (!providerConfig) {
      out.push({ index, name: agent.name, provider, modelId, reason: 'missing_provider' })
      return
    }
    const found = providerConfig.models.some((m) => m.id === modelId)
    if (!found) {
      out.push({ index, name: agent.name, provider, modelId, reason: 'missing_model' })
    }
  })
  return out
}

// ── Step 5: Theme ────────────────────────────────────────────

export function validateThemeStep(): StepValidationResult {
  // Theme settings always have defaults, so this step is always valid.
  return VALID
}
