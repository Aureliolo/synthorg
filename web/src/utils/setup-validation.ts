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

// `Intl.Segmenter` with `granularity: 'grapheme'` counts user-visible
// characters: a ZWJ-joined emoji (e.g. `👨‍💻`) is one grapheme, a base
// letter plus combining mark (`é` as `é`) is one grapheme, and a
// regional-indicator flag (`🇺🇸`) is one grapheme. `.length` would count
// UTF-16 code units (8, 2, 4 respectively) and spread iteration would
// count code points (3, 2, 2), both of which under-report against the
// 200-grapheme limit. Falls back to spread iteration on the rare
// runtimes that don't expose `Intl.Segmenter`.
const graphemeSegmenter =
  typeof Intl !== 'undefined' && 'Segmenter' in Intl
    ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    : null

export function graphemeLength(s: string): number {
  if (graphemeSegmenter) {
    let count = 0
    const iterator = graphemeSegmenter.segment(s)[Symbol.iterator]()
    while (!iterator.next().done) count += 1
    return count
  }
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
  const unresolved: UnresolvedAgent[] = []

  for (let index = 0; index < agents.length; index += 1) {
    const agent = agents[index]!
    const provider = agent.model_provider
    const modelId = agent.model_id

    if (!provider || !modelId) {
      unresolved.push({
        index,
        name: agent.name,
        provider: provider ?? null,
        modelId: modelId ?? null,
        reason: 'unassigned',
      })
      continue
    }

    // Use `Object.hasOwn` (not bracket lookup) so an agent referencing a
    // provider whose name collides with an `Object.prototype` member
    // (e.g. `valueOf`, `toString`) is reported as `missing_provider`
    // rather than crashing on `.models.some` against the inherited
    // prototype member.
    if (!Object.hasOwn(providers, provider)) {
      unresolved.push({
        index,
        name: agent.name,
        provider,
        modelId,
        reason: 'missing_provider',
      })
      continue
    }

    const providerConfig = providers[provider]!
    const found = providerConfig.models.some((m) => m.id === modelId)
    if (!found) {
      unresolved.push({
        index,
        name: agent.name,
        provider,
        modelId,
        reason: 'missing_model',
      })
    }
  }

  return unresolved
}

// ── Step 5: Theme ────────────────────────────────────────────

export function validateThemeStep(): StepValidationResult {
  // Theme settings always have defaults, so this step is always valid.
  return VALID
}
