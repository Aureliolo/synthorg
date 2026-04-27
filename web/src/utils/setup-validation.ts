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

// ── Step 0: Account ──────────────────────────────────────────

interface AccountStepInput {
  readonly accountCreated: boolean
  readonly needsAdmin: boolean
}

export function validateAccountStep(input: AccountStepInput): StepValidationResult {
  if (!input.needsAdmin) return VALID
  if (input.accountCreated) return VALID
  return invalid('Admin account must be created')
}

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

export function validateCompanyStep(input: CompanyStepInput): CompanyStepValidationResult {
  const errors: string[] = []
  const trimmedName = input.companyName.trim()

  if (!trimmedName) {
    errors.push('Company name is required')
  } else if (trimmedName.length > MAX_COMPANY_NAME_LENGTH) {
    errors.push(`Company name must be ${MAX_COMPANY_NAME_LENGTH} characters or less`)
  }

  if (input.companyDescription.trim().length > MAX_DESCRIPTION_LENGTH) {
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
  readonly agents: readonly SetupAgentSummary[]
  readonly providers: Readonly<Record<string, ProviderConfig>>
}

export function validateProvidersStep(input: ProvidersStepInput): StepValidationResult {
  const errors: string[] = []
  const providerNames = Object.keys(input.providers)

  if (providerNames.length === 0) {
    errors.push('At least one provider is required')
    return { valid: false, errors }
  }

  // Every configured provider must expose at least one model. A
  // provider that the user adds without successful model discovery is
  // dead weight: the wizard's downstream agent step needs at least
  // one model to wire each agent's ``model`` field to. Surface this
  // as a step-level validation error so the wizard's ``Next`` button
  // stays disabled until the user runs discovery / configures models
  // manually.
  const emptyProviders: string[] = []
  for (const [name, provider] of Object.entries(input.providers)) {
    if (provider.models.length === 0) {
      emptyProviders.push(name)
    }
  }
  for (const name of emptyProviders) {
    errors.push(
      `Provider "${name}" has no models. Run model discovery or add a model manually before continuing.`,
    )
  }

  // Agents that reference a model on a configured provider must
  // actually find that model in the provider's model list. A typo in
  // the model name (or a provider that lost its model in a refresh)
  // would otherwise let setup complete with an agent pointing at a
  // non-existent model and surface as a cryptic "model not found"
  // error on the first task. Both halves of the check (missing
  // provider, missing model on existing provider) live here so the
  // wizard's ``Next`` button gates on either failure mode.
  const providerSet = new Set(providerNames)
  const missingProviders = new Set<string>()
  const missingModels: string[] = []

  for (const agent of input.agents) {
    if (agent.model_provider && !providerSet.has(agent.model_provider)) {
      missingProviders.add(agent.model_provider)
      continue
    }
    if (agent.model_provider && agent.model_id) {
      const provider = input.providers[agent.model_provider]
      const modelIds = new Set(provider?.models.map((m) => m.id) ?? [])
      if (!modelIds.has(agent.model_id)) {
        missingModels.push(
          `Agent "${agent.name}" references model "${agent.model_id}" on "${agent.model_provider}", but that provider does not expose it.`,
        )
      }
    }
  }

  for (const name of missingProviders) {
    errors.push(`Provider "${name}" is referenced by agents but not configured`)
  }
  errors.push(...missingModels)

  return errors.length > 0 ? { valid: false, errors } : VALID
}

// ── Step 5: Theme ────────────────────────────────────────────

export function validateThemeStep(): StepValidationResult {
  // Theme settings always have defaults, so this step is always valid.
  return VALID
}
