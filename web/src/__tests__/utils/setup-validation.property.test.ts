import fc from 'fast-check'
import {
  validateCompanyStep,
  validateAgentsStep,
  validateProvidersStep,
} from '@/utils/setup-validation'
import type { ProviderConfig } from '@/api/types/providers'
import type { SetupAgentSummary, SetupCompanyResponse } from '@/api/types/setup'

const makeAgent = (overrides: Partial<SetupAgentSummary> = {}): SetupAgentSummary => ({
  name: 'Agent',
  role: 'Dev',
  department: 'eng',
  level: 'mid',
  model_provider: 'test-provider',
  model_id: 'test-model',
  tier: 'medium',
  personality_preset: null,
  ...overrides,
})

const makeCompanyResponse = (): SetupCompanyResponse => ({
  company_name: 'Co',
  description: null,
  template_applied: 'startup',
  department_count: 1,
  agent_count: 1,
  agents: [],
})

const makeProvider = (modelIds: readonly string[] = ['test-model-001']): ProviderConfig => ({
  driver: 'test-provider',
  litellm_provider: null,
  auth_type: 'api_key',
  base_url: null,
  models: modelIds.map((id) => ({
    id,
    alias: null,
    max_context: 8192,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    estimated_latency_ms: null,
    local_params: null,
  })),
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
})

describe('setup-validation property tests', () => {
  it('company name with 1-200 non-whitespace chars + response is always valid', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 250 }).filter(
          (s) => s.trim().length > 0 && s.trim().length <= 200,
        ),
        (name) => {
          const result = validateCompanyStep({
            companyName: name,
            companyDescription: '',
            companyResponse: makeCompanyResponse(),
          })
          expect(result.valid).toBe(true)
        },
      ),
    )
  })

  it('empty or whitespace-only company names are always invalid', () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^\s{0,50}$/),
        (name) => {
          const result = validateCompanyStep({
            companyName: name,
            companyDescription: '',
            companyResponse: makeCompanyResponse(),
          })
          expect(result.valid).toBe(false)
        },
      ),
    )
  })

  it('agents with non-empty model_provider and model_id are always valid', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            provider: fc.string({ minLength: 1, maxLength: 30 }),
            modelId: fc.string({ minLength: 1, maxLength: 30 }),
          }),
          { minLength: 1, maxLength: 10 },
        ),
        (specs) => {
          const agents = specs.map((s) =>
            makeAgent({ model_provider: s.provider, model_id: s.modelId }),
          )
          const result = validateAgentsStep({ agents })
          expect(result.valid).toBe(true)
        },
      ),
    )
  })

  it('providers step is valid when all referenced providers exist with at least one model that the agent references', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.string({ minLength: 1, maxLength: 20 }),
          { minLength: 1, maxLength: 5 },
        ),
        (providerNames) => {
          const unique = [...new Set(providerNames)]
          const agents = unique.map((p) =>
            makeAgent({ model_provider: p, model_id: 'test-model-001' }),
          )
          const providers: Record<string, ProviderConfig> = Object.create(null) as Record<string, ProviderConfig>
          for (const name of unique) {
            providers[name] = makeProvider()
          }
          const result = validateProvidersStep({ agents, providers })
          expect(result.valid).toBe(true)
        },
      ),
    )
  })

  it('validation result always has errors array', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        (hasResponse) => {
          const result = validateCompanyStep({
            companyName: 'Test',
            companyDescription: '',
            companyResponse: hasResponse ? makeCompanyResponse() : null,
          })
          expect(Array.isArray(result.errors)).toBe(true)
        },
      ),
    )
  })
})
