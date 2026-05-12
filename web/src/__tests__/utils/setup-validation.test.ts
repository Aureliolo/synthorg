import {
  validateAccountStep,
  validateTemplateStep,
  validateCompanyStep,
  validateAgentsStep,
  validateProvidersStep,
  validateThemeStep,
  resolveAgentModels,
} from '@/utils/setup-validation'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'
import type { SetupAgentSummary, SetupCompanyResponse } from '@/api/types/setup'

const makeAgent = (overrides: Partial<SetupAgentSummary> = {}): SetupAgentSummary => ({
  name: 'Test Agent',
  role: 'Developer',
  department: 'engineering',
  level: 'mid',
  model_provider: 'test-provider',
  model_id: 'test-model-001',
  tier: 'medium',
  personality_preset: null,
  ...overrides,
})

const makeCompanyResponse = (
  overrides: Partial<SetupCompanyResponse> = {},
): SetupCompanyResponse => ({
  company_name: 'Acme Corp',
  description: null,
  template_applied: 'startup',
  department_count: 3,
  agent_count: 5,
  agents: [],
  ...overrides,
})

const makeModel = (overrides: Partial<ProviderModelConfig> = {}): ProviderModelConfig => ({
  id: 'test-model-001',
  alias: null,
  max_context: 8192,
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  estimated_latency_ms: null,
  local_params: null,
  ...overrides,
})

const makeProvider = (overrides: Partial<ProviderConfig> = {}): ProviderConfig => ({
  driver: 'test-provider',
  litellm_provider: null,
  auth_type: 'api_key',
  base_url: null,
  models: [makeModel()],
  has_api_key: true,
  has_oauth_credentials: false,
  has_custom_header: false,
  has_subscription_token: false,
  tos_accepted_at: null,
  oauth_token_url: null,
  oauth_client_id: null,
  oauth_scope: null,
  custom_header_name: null,
  preset_name: null,
  supports_model_pull: false,
  supports_model_delete: false,
  supports_model_config: false,
  ...overrides,
})

describe('validateAccountStep', () => {
  it('returns valid when accountCreated is true', () => {
    const result = validateAccountStep({ accountCreated: true, needsAdmin: true })
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it('returns valid when needsAdmin is false (skip account step)', () => {
    const result = validateAccountStep({ accountCreated: false, needsAdmin: false })
    expect(result.valid).toBe(true)
  })

  it('returns invalid when needsAdmin and account not created', () => {
    const result = validateAccountStep({ accountCreated: false, needsAdmin: true })
    expect(result.valid).toBe(false)
    expect(result.errors.length).toBeGreaterThan(0)
  })
})

describe('validateTemplateStep', () => {
  it('returns valid when template is selected', () => {
    const result = validateTemplateStep({ selectedTemplate: 'startup' })
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it('returns invalid when no template selected', () => {
    const result = validateTemplateStep({ selectedTemplate: null })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('Please select a template')
  })
})

describe('validateCompanyStep', () => {
  it('returns valid with company name and response', () => {
    const result = validateCompanyStep({
      companyName: 'Acme Corp',
      companyDescription: '',
      companyResponse: makeCompanyResponse(),
    })
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it('returns invalid when company name is empty', () => {
    const result = validateCompanyStep({
      companyName: '',
      companyDescription: '',
      companyResponse: null,
    })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('Company name is required')
  })

  it('returns invalid when company name is only whitespace', () => {
    const result = validateCompanyStep({
      companyName: '   ',
      companyDescription: '',
      companyResponse: null,
    })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('Company name is required')
  })

  it('returns invalid when company name exceeds 200 characters', () => {
    const result = validateCompanyStep({
      companyName: 'A'.repeat(201),
      companyDescription: '',
      companyResponse: null,
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('200'))).toBe(true)
  })

  it('returns invalid when description exceeds 1000 characters', () => {
    const result = validateCompanyStep({
      companyName: 'Acme',
      companyDescription: 'A'.repeat(1001),
      companyResponse: null,
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('1000'))).toBe(true)
  })

  it('returns invalid when template not yet applied (no response)', () => {
    const result = validateCompanyStep({
      companyName: 'Acme',
      companyDescription: '',
      companyResponse: null,
    })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('Apply the template to continue')
  })
})

describe('validateAgentsStep', () => {
  it('returns valid when agents have required fields', () => {
    const result = validateAgentsStep({
      agents: [makeAgent(), makeAgent({ name: 'Agent 2' })],
    })
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it('returns invalid when agent list is empty', () => {
    const result = validateAgentsStep({ agents: [] })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('At least one agent is required')
  })

  it('returns invalid when an agent has no model_provider', () => {
    const result = validateAgentsStep({
      agents: [makeAgent({ model_provider: '' })],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('model'))).toBe(true)
  })

  it('returns invalid when an agent has no model_id', () => {
    const result = validateAgentsStep({
      agents: [makeAgent({ model_id: '' })],
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('model'))).toBe(true)
  })
})

describe('validateProvidersStep', () => {
  it('returns valid when at least one provider with at least one model is configured', () => {
    const result = validateProvidersStep({
      providers: { 'test-provider': makeProvider() },
    })
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })

  it('returns invalid when no providers configured', () => {
    const result = validateProvidersStep({ providers: {} })
    expect(result.valid).toBe(false)
    expect(result.errors).toContain('At least one provider is required')
  })

  it('returns valid with multiple providers each exposing models', () => {
    const result = validateProvidersStep({
      providers: {
        'provider-a': makeProvider(),
        'provider-b': makeProvider(),
      },
    })
    expect(result.valid).toBe(true)
  })

  it('returns invalid when a configured provider has no models', () => {
    const result = validateProvidersStep({
      providers: { 'empty-provider': makeProvider({ models: [] }) },
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('no models'))).toBe(true)
  })

  it('names the specific empty provider when only one of multiple is empty', () => {
    const result = validateProvidersStep({
      providers: {
        'provider-a': makeProvider(),
        'provider-empty': makeProvider({ models: [] }),
      },
    })
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('provider-empty'))).toBe(true)
    expect(result.errors.every((e) => !e.includes('"provider-a"'))).toBe(true)
  })
})

describe('resolveAgentModels', () => {
  it('returns empty array when every agent resolves cleanly', () => {
    const result = resolveAgentModels(
      [makeAgent({ model_provider: 'test-provider', model_id: 'test-model-001' })],
      { 'test-provider': makeProvider() },
    )
    expect(result).toHaveLength(0)
  })

  it("flags an agent with no model_provider / model_id as 'unassigned'", () => {
    const result = resolveAgentModels(
      [makeAgent({ name: 'Alice', model_provider: null, model_id: null })],
      { 'test-provider': makeProvider() },
    )
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ name: 'Alice', reason: 'unassigned' })
  })

  it("flags an agent referencing an unknown provider as 'missing_provider'", () => {
    const result = resolveAgentModels(
      [makeAgent({ name: 'Bob', model_provider: 'gone', model_id: 'm-1' })],
      { 'cloud-x': makeProvider() },
    )
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ name: 'Bob', provider: 'gone', reason: 'missing_provider' })
  })

  it("flags an agent referencing a missing model on a configured provider as 'missing_model'", () => {
    const result = resolveAgentModels(
      [
        makeAgent({
          name: 'Carol',
          model_provider: 'test-provider',
          model_id: 'model-not-on-provider',
        }),
      ],
      {
        'test-provider': makeProvider({ models: [makeModel({ id: 'test-model-001' })] }),
      },
    )
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({
      name: 'Carol',
      provider: 'test-provider',
      modelId: 'model-not-on-provider',
      reason: 'missing_model',
    })
  })

  it('preserves agent index across the agents array', () => {
    const result = resolveAgentModels(
      [
        makeAgent({ name: 'A', model_provider: 'test-provider', model_id: 'test-model-001' }),
        makeAgent({ name: 'B', model_provider: null, model_id: null }),
        makeAgent({ name: 'C', model_provider: 'test-provider', model_id: 'test-model-001' }),
      ],
      { 'test-provider': makeProvider() },
    )
    expect(result).toHaveLength(1)
    expect(result[0]?.index).toBe(1)
  })
})

describe('validateThemeStep', () => {
  it('always returns valid (all settings have defaults)', () => {
    const result = validateThemeStep()
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
  })
})
