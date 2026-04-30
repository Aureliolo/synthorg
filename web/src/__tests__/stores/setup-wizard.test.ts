import { http, HttpResponse } from 'msw'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { apiError, apiSuccess, buildLocalPreset, buildCloudPreset } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { CURRENCY_OPTIONS, DEFAULT_CURRENCY } from '@/utils/currencies'
import type { SeniorityLevel } from '@/api/types/enums'

const _NON_DEFAULT = CURRENCY_OPTIONS.find((c) => c.value !== DEFAULT_CURRENCY)
if (!_NON_DEFAULT) {
  throw new Error(
    'CURRENCY_OPTIONS must contain at least one non-default currency ' +
      'for this test; update @/utils/currencies.',
  )
}
const NON_DEFAULT_CURRENCY = _NON_DEFAULT.value

function resetStore() {
  useSetupWizardStore.getState().reset()
}

describe('setup wizard store', () => {
  beforeEach(() => {
    resetStore()
  })

  describe('initialization', () => {
    it('initializes with mode as first step when needsAdmin is false', () => {
      const state = useSetupWizardStore.getState()
      expect(state.needsAdmin).toBe(false)
      expect(state.stepOrder[0]).toBe('mode')
      expect(state.stepOrder).not.toContain('account')
    })

    it('has all steps completed set to false', () => {
      const state = useSetupWizardStore.getState()
      for (const step of state.stepOrder) {
        expect(state.stepsCompleted[step]).toBe(false)
      }
    })

    it('has DEFAULT_CURRENCY as default', () => {
      expect(useSetupWizardStore.getState().currency).toBe(DEFAULT_CURRENCY)
    })

    it('has no template selected', () => {
      expect(useSetupWizardStore.getState().selectedTemplate).toBeNull()
    })
  })

  describe('navigation', () => {
    it('sets current step', () => {
      useSetupWizardStore.getState().setStep('company')
      expect(useSetupWizardStore.getState().currentStep).toBe('company')
    })

    it('sets direction to forward when advancing', () => {
      useSetupWizardStore.getState().setStep('company')
      expect(useSetupWizardStore.getState().direction).toBe('forward')
    })

    it('sets direction to backward when going back', () => {
      useSetupWizardStore.getState().setStep('company')
      useSetupWizardStore.getState().setStep('template')
      expect(useSetupWizardStore.getState().direction).toBe('backward')
    })

    it('marks step as complete', () => {
      useSetupWizardStore.getState().markStepComplete('template')
      expect(useSetupWizardStore.getState().stepsCompleted.template).toBe(true)
    })

    it('marks step as incomplete', () => {
      useSetupWizardStore.getState().markStepComplete('template')
      useSetupWizardStore.getState().markStepIncomplete('template')
      expect(useSetupWizardStore.getState().stepsCompleted.template).toBe(false)
    })

    it('canNavigateTo returns true for first step', () => {
      expect(useSetupWizardStore.getState().canNavigateTo('mode')).toBe(true)
    })

    it('canNavigateTo returns false for later steps when prior are incomplete', () => {
      expect(useSetupWizardStore.getState().canNavigateTo('template')).toBe(false)
    })

    it('canNavigateTo returns true when all prior steps are complete', () => {
      useSetupWizardStore.getState().markStepComplete('mode')
      expect(useSetupWizardStore.getState().canNavigateTo('template')).toBe(true)
    })

    it('canNavigateTo checks all prior steps', () => {
      useSetupWizardStore.getState().markStepComplete('mode')
      useSetupWizardStore.getState().markStepComplete('template')
      expect(useSetupWizardStore.getState().canNavigateTo('providers')).toBe(false)
    })
  })

  describe('dynamic step order', () => {
    it('includes account step when needsAdmin is true', () => {
      useSetupWizardStore.getState().setNeedsAdmin(true)
      const state = useSetupWizardStore.getState()
      expect(state.stepOrder[0]).toBe('account')
      expect(state.stepOrder).toContain('account')
    })

    it('excludes account step when needsAdmin is false', () => {
      useSetupWizardStore.getState().setNeedsAdmin(false)
      const state = useSetupWizardStore.getState()
      expect(state.stepOrder).not.toContain('account')
    })

    it('sets quick mode step order when setWizardMode("quick") is called', () => {
      useSetupWizardStore.getState().setWizardMode('quick')
      const state = useSetupWizardStore.getState()
      expect(state.stepOrder).toEqual(['mode', 'company', 'providers', 'complete'])
      expect(state.wizardMode).toBe('quick')
    })

    it('restores guided mode step order when setWizardMode("guided") is called', () => {
      useSetupWizardStore.getState().setWizardMode('quick')
      useSetupWizardStore.getState().setWizardMode('guided')
      const state = useSetupWizardStore.getState()
      expect(state.stepOrder).toEqual([
        'mode',
        'template',
        'company',
        'providers',
        'agents',
        'theme',
        'complete',
      ])
      expect(state.wizardMode).toBe('guided')
    })

    it('clears template state when switching to quick mode', () => {
      useSetupWizardStore.setState({ selectedTemplate: 'startup' })
      useSetupWizardStore.getState().setWizardMode('quick')
      expect(useSetupWizardStore.getState().selectedTemplate).toBeNull()
    })

    it('setStep ignores steps not in current flow', () => {
      useSetupWizardStore.getState().setWizardMode('quick')
      const before = useSetupWizardStore.getState().currentStep
      useSetupWizardStore.getState().setStep('agents')
      expect(useSetupWizardStore.getState().currentStep).toBe(before)
    })

    it('includes account in quick mode when needsAdmin is true', () => {
      useSetupWizardStore.getState().setNeedsAdmin(true)
      useSetupWizardStore.getState().setWizardMode('quick')
      const state = useSetupWizardStore.getState()
      expect(state.stepOrder).toContain('account')
      expect(state.stepOrder).toEqual([
        'account',
        'mode',
        'company',
        'providers',
        'complete',
      ])
    })
  })

  describe('template actions', () => {
    it('selects a template', () => {
      useSetupWizardStore.getState().selectTemplate('startup')
      expect(useSetupWizardStore.getState().selectedTemplate).toBe('startup')
    })

    it('toggles compare on', () => {
      useSetupWizardStore.getState().toggleCompare('startup')
      expect(useSetupWizardStore.getState().comparedTemplates).toContain('startup')
    })

    it('toggles compare off', () => {
      useSetupWizardStore.getState().toggleCompare('startup')
      useSetupWizardStore.getState().toggleCompare('startup')
      expect(useSetupWizardStore.getState().comparedTemplates).not.toContain('startup')
    })

    it('limits comparison to 3 templates', () => {
      useSetupWizardStore.getState().toggleCompare('a')
      useSetupWizardStore.getState().toggleCompare('b')
      useSetupWizardStore.getState().toggleCompare('c')
      const added = useSetupWizardStore.getState().toggleCompare('d')
      expect(added).toBe(false)
      expect(useSetupWizardStore.getState().comparedTemplates).toHaveLength(3)
    })

    it('clears comparison', () => {
      useSetupWizardStore.getState().toggleCompare('a')
      useSetupWizardStore.getState().toggleCompare('b')
      useSetupWizardStore.getState().clearComparison()
      expect(useSetupWizardStore.getState().comparedTemplates).toHaveLength(0)
    })

    it('fetches templates from API', async () => {
      server.use(
        http.get('/api/v1/setup/templates', () =>
          HttpResponse.json(
            apiSuccess([
              {
                name: 'startup',
                display_name: 'Tech Startup',
                description: 'A startup template',
                source: 'builtin',
                tags: ['startup'],
                skill_patterns: [],
                variables: [],
                agent_count: 5,
                department_count: 3,
                autonomy_level: 'semi',
                workflow: 'agile_kanban',
              },
            ]),
          ),
        ),
      )

      await useSetupWizardStore.getState().fetchTemplates()

      const state = useSetupWizardStore.getState()
      expect(state.templates).toHaveLength(1)
      expect(state.templates[0]?.name).toBe('startup')
      expect(state.templates[0]?.agent_count).toBe(5)
      expect(state.templates[0]?.department_count).toBe(3)
      expect(state.templates[0]?.autonomy_level).toBe('semi')
      expect(state.templates[0]?.workflow).toBe('agile_kanban')
      expect(state.templatesLoading).toBe(false)
      expect(state.templatesError).toBeNull()
    })

    it('sets error on fetch failure', async () => {
      server.use(
        http.get('/api/v1/setup/templates', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )

      await useSetupWizardStore.getState().fetchTemplates()

      const state = useSetupWizardStore.getState()
      expect(state.templatesError).toBe('Network error')
      expect(state.templatesLoading).toBe(false)
    })
  })

  describe('company actions', () => {
    it('sets company name', () => {
      useSetupWizardStore.getState().setCompanyName('Acme Corp')
      expect(useSetupWizardStore.getState().companyName).toBe('Acme Corp')
    })

    it('sets currency', () => {
      useSetupWizardStore.getState().setCurrency('USD')
      expect(useSetupWizardStore.getState().currency).toBe('USD')
    })

    it('submits company and stores response', async () => {
      server.use(
        http.post('/api/v1/setup/company', () =>
          HttpResponse.json(
            apiSuccess({
              company_name: 'Acme Corp',
              description: null,
              template_applied: 'startup',
              department_count: 3,
              agent_count: 5,
              agents: [
                {
                  name: 'CEO',
                  role: 'CEO',
                  department: 'executive',
                  level: 'c_suite' as SeniorityLevel,
                  model_provider: 'test-provider',
                  model_id: 'test-model',
                  tier: 'large',
                  personality_preset: 'visionary_leader',
                },
              ],
            }),
            { status: 201 },
          ),
        ),
      )

      useSetupWizardStore.setState({
        companyName: 'Acme Corp',
        selectedTemplate: 'startup',
      })
      await useSetupWizardStore.getState().submitCompany()

      const state = useSetupWizardStore.getState()
      expect(state.companyResponse).toBeDefined()
      expect(state.companyResponse?.company_name).toBe('Acme Corp')
      expect(state.agents).toHaveLength(1)
      expect(state.companyLoading).toBe(false)
    })

    it('captures the structured error_code on tier_coverage_insufficient', async () => {
      // Backend returns a 422 with the discriminated error_code 2004
      // (PROVIDER_TIER_COVERAGE_INSUFFICIENT). The store stores it
      // verbatim so the page can surface a "Go back to Providers
      // step" affordance instead of a generic Retry button.
      server.use(
        http.post('/api/v1/setup/company', () =>
          HttpResponse.json(
            apiError(
              'No configured provider exposes any models. Go back to '
              + 'the Providers step, add at least one model to a provider, '
              + 'then return here to apply the template.',
              { error_code: 2004, error_category: 'validation' },
            ),
            { status: 422 },
          ),
        ),
      )

      useSetupWizardStore.setState({
        companyName: 'Acme Corp',
        selectedTemplate: 'startup',
      })
      await useSetupWizardStore.getState().submitCompany()

      const state = useSetupWizardStore.getState()
      expect(state.companyResponse).toBeNull()
      expect(state.companyError).toContain('Providers step')
      expect(state.companyErrorCode).toBe(2004)
      expect(state.companyLoading).toBe(false)
    })

    it('captures null error_code for non-discriminated errors', async () => {
      server.use(
        http.post('/api/v1/setup/company', () =>
          HttpResponse.error(),
        ),
      )
      useSetupWizardStore.setState({
        companyName: 'Acme Corp',
        selectedTemplate: 'startup',
      })
      await useSetupWizardStore.getState().submitCompany()
      expect(useSetupWizardStore.getState().companyErrorCode).toBeNull()
    })

    it('clears companyErrorCode on the next attempt', async () => {
      server.use(
        http.post('/api/v1/setup/company', () =>
          HttpResponse.json(
            apiError('insufficient', { error_code: 2004 }),
            { status: 422 },
          ),
        ),
      )
      useSetupWizardStore.setState({
        companyName: 'Acme Corp',
        selectedTemplate: 'startup',
      })
      await useSetupWizardStore.getState().submitCompany()
      expect(useSetupWizardStore.getState().companyErrorCode).toBe(2004)

      // Next attempt: success. Code must clear (not linger from prior).
      server.use(
        http.post('/api/v1/setup/company', () =>
          HttpResponse.json(
            apiSuccess({
              company_name: 'Acme Corp',
              description: null,
              template_applied: 'startup',
              department_count: 1,
              agent_count: 0,
              agents: [],
            }),
            { status: 201 },
          ),
        ),
      )
      await useSetupWizardStore.getState().submitCompany()
      expect(useSetupWizardStore.getState().companyErrorCode).toBeNull()
      expect(useSetupWizardStore.getState().companyError).toBeNull()
    })

    it('blocks a re-entrant submitCompany while one is already in flight', async () => {
      // Concurrent calls must coalesce into a single POST so a
      // double-click (or programmatic re-entry) cannot duplicate the
      // company creation.
      let inflightCount = 0
      let observedConcurrent = 0
      let totalCalls = 0
      server.use(
        http.post('/api/v1/setup/company', async () => {
          totalCalls += 1
          inflightCount += 1
          observedConcurrent = Math.max(observedConcurrent, inflightCount)
          await new Promise((resolve) => setTimeout(resolve, 20))
          inflightCount -= 1
          return HttpResponse.json(
            apiSuccess({
              company_name: 'Acme Corp',
              description: null,
              template_applied: 'startup',
              department_count: 1,
              agent_count: 0,
              agents: [],
            }),
            { status: 201 },
          )
        }),
      )

      useSetupWizardStore.setState({
        companyName: 'Acme Corp',
        selectedTemplate: 'startup',
      })
      await Promise.all([
        useSetupWizardStore.getState().submitCompany(),
        useSetupWizardStore.getState().submitCompany(),
        useSetupWizardStore.getState().submitCompany(),
      ])
      // ``observedConcurrent`` alone is satisfied if a serial test
      // runner happens to schedule the three calls one-after-another
      // (no real concurrency to block). ``totalCalls`` pins the
      // contract: only one POST must reach the server even when
      // three calls are issued in the same tick.
      expect(observedConcurrent).toBe(1)
      expect(totalCalls).toBe(1)
    })
  })

  describe('agent actions', () => {
    it('updates agent name via API', async () => {
      const updatedAgent = {
        name: 'New Name',
        role: 'CEO',
        department: 'executive',
        level: 'c_suite' as SeniorityLevel,
        model_provider: 'p',
        model_id: 'm',
        tier: 'large',
        personality_preset: null,
      }
      server.use(
        http.put('/api/v1/setup/agents/:index/name', () =>
          HttpResponse.json(apiSuccess(updatedAgent)),
        ),
      )

      useSetupWizardStore.setState({
        agents: [
          {
            name: 'Old Name',
            role: 'CEO',
            department: 'executive',
            level: 'c_suite' as SeniorityLevel,
            model_provider: 'p',
            model_id: 'm',
            tier: 'large',
            personality_preset: null,
          },
        ],
      })

      await useSetupWizardStore.getState().updateAgentName(0, 'New Name')
      expect(useSetupWizardStore.getState().agents[0]?.name).toBe('New Name')
    })
  })

  describe('theme settings', () => {
    it('updates theme setting', () => {
      useSetupWizardStore.getState().setThemeSetting('density', 'dense')
      expect(useSetupWizardStore.getState().themeSettings.density).toBe('dense')
    })

    it('preserves other theme settings when updating one', () => {
      useSetupWizardStore.getState().setThemeSetting('density', 'dense')
      useSetupWizardStore.getState().setThemeSetting('animation', 'spring')
      const state = useSetupWizardStore.getState()
      expect(state.themeSettings.density).toBe('dense')
      expect(state.themeSettings.animation).toBe('spring')
    })
  })

  describe('reset', () => {
    it('resets all state to initial values', () => {
      useSetupWizardStore.setState({
        selectedTemplate: 'startup',
        companyName: 'Acme',
        currency: NON_DEFAULT_CURRENCY,
      })
      useSetupWizardStore.getState().reset()

      const state = useSetupWizardStore.getState()
      expect(state.selectedTemplate).toBeNull()
      expect(state.companyName).toBe('')
      expect(state.currency).toBe(DEFAULT_CURRENCY)
    })
  })

  describe('provider actions (full)', () => {
    const mockProvider = {
      driver: 'litellm',
      litellm_provider: 'test-provider',
      auth_type: 'api_key' as const,
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
      base_url: 'https://api.example.com',
      models: [
        {
          id: 'test-model-001',
          alias: null,
          cost_per_1k_input: 0,
          cost_per_1k_output: 0,
          max_context: 128000,
          estimated_latency_ms: null,
          local_params: null,
        },
      ],
    }

    it('createProviderFromPresetFull stores provider on success', async () => {
      server.use(
        http.post('/api/v1/providers/from-preset', () =>
          HttpResponse.json(apiSuccess(mockProvider), { status: 201 }),
        ),
      )

      const result = await useSetupWizardStore
        .getState()
        .createProviderFromPresetFull({
          preset_name: 'test-preset',
          name: 'my-provider',
          api_key: 'sk-test',
        })

      expect(result).toEqual(mockProvider)
      expect(
        useSetupWizardStore.getState().providers['my-provider'],
      ).toEqual(mockProvider)
      expect(useSetupWizardStore.getState().providersError).toBeNull()
    })

    it('createProviderFromPresetFull triggers discovery for zero-model providers', async () => {
      const emptyProvider = {
        ...mockProvider,
        litellm_provider: 'test-local',
        auth_type: 'none' as const,
        has_api_key: false,
        models: [],
      }
      const refreshedProvider = {
        ...emptyProvider,
        models: [
          {
            id: 'test-model-001',
            alias: null,
            cost_per_1k_input: 0,
            cost_per_1k_output: 0,
            max_context: 128000,
            estimated_latency_ms: null,
            local_params: null,
          },
        ],
      }
      let discoverCalls = 0
      let getProviderCalls = 0
      server.use(
        http.post('/api/v1/providers/from-preset', () =>
          HttpResponse.json(apiSuccess(emptyProvider), { status: 201 }),
        ),
        http.post('/api/v1/providers/:name/discover-models', () => {
          discoverCalls += 1
          return HttpResponse.json(
            apiSuccess({
              discovered_models: [],
              provider_name: 'local-provider',
            }),
          )
        }),
        http.get('/api/v1/providers/:name', () => {
          getProviderCalls += 1
          return HttpResponse.json(apiSuccess(refreshedProvider))
        }),
      )

      const result = await useSetupWizardStore
        .getState()
        .createProviderFromPresetFull({
          preset_name: 'test-local',
          name: 'local-provider',
        })

      expect(discoverCalls).toBeGreaterThan(0)
      expect(getProviderCalls).toBeGreaterThan(0)
      expect(result).toEqual(refreshedProvider)
      expect(
        useSetupWizardStore.getState().providers['local-provider'],
      ).toEqual(refreshedProvider)
    })

    it('createProviderFromPresetFull returns null and sets error on failure', async () => {
      server.use(
        http.post('/api/v1/providers/from-preset', () =>
          HttpResponse.json(apiError('Auth failed')),
        ),
      )

      const result = await useSetupWizardStore
        .getState()
        .createProviderFromPresetFull({
          preset_name: 'test-preset',
          name: 'my-provider',
        })

      expect(result).toBeNull()
      expect(useSetupWizardStore.getState().providersError).toBe('Auth failed')
    })

    it('createProviderCustom stores provider on success', async () => {
      const customProvider = {
        ...mockProvider,
        driver: 'custom',
        litellm_provider: 'custom',
        auth_type: 'none' as const,
        has_api_key: false,
        base_url: 'http://localhost:8000',
        models: [],
      }
      server.use(
        http.post('/api/v1/providers', () =>
          HttpResponse.json(apiSuccess(customProvider), { status: 201 }),
        ),
      )

      const result = await useSetupWizardStore
        .getState()
        .createProviderCustom({
          name: 'custom-provider',
          auth_type: 'none',
          base_url: 'http://localhost:8000',
        })

      expect(result).toEqual(customProvider)
      expect(
        useSetupWizardStore.getState().providers['custom-provider'],
      ).toEqual(customProvider)
    })

    it('createProviderCustom returns null and sets error on failure', async () => {
      server.use(
        http.post('/api/v1/providers', () =>
          HttpResponse.json(apiError('Connection refused')),
        ),
      )

      const result = await useSetupWizardStore
        .getState()
        .createProviderCustom({
          name: 'bad-provider',
          auth_type: 'none',
        })

      expect(result).toBeNull()
      expect(useSetupWizardStore.getState().providersError).toBe(
        'Connection refused',
      )
    })
  })

  describe('local provider probe (batch endpoint)', () => {
    // Use the shared builders so the preset shape stays aligned with the
    // real /providers/presets response; test-local drift is only the
    // name + provider override per preset.
    const PRESET_FIXTURES = [
      buildLocalPreset({
        name: 'ollama',
        display_name: 'Ollama',
        litellm_provider: 'ollama',
        default_base_url: 'http://localhost:11434',
      }),
      buildCloudPreset({
        name: 'openrouter',
        display_name: 'OpenRouter',
        litellm_provider: 'openrouter',
        default_base_url: 'https://openrouter.ai/api/v1',
      }),
    ]

    function seedPresets() {
      server.use(
        http.get('/api/v1/providers/presets', () =>
          HttpResponse.json(apiSuccess(PRESET_FIXTURES)),
        ),
      )
      return useSetupWizardStore.getState().fetchPresets()
    }

    it('populates probeResults from the batch envelope on success', async () => {
      await seedPresets()
      server.use(
        http.post('/api/v1/providers/probe-local', () =>
          HttpResponse.json(
            apiSuccess({
              results: {
                ollama: {
                  url: 'http://localhost:11434',
                  model_count: 3,
                  candidates_tried: 1,
                },
              },
              errors: {},
            }),
          ),
        ),
      )

      await useSetupWizardStore.getState().probeLocalProviders()

      const state = useSetupWizardStore.getState()
      expect(state.probeResults['ollama']).toMatchObject({ model_count: 3 })
      expect(state.probeErrors).toEqual({})
      expect(state.probeGlobalError).toBeNull()
    })

    it('forwards per-preset failures from the batch envelope into probeErrors', async () => {
      await seedPresets()
      server.use(
        http.post('/api/v1/providers/probe-local', () =>
          HttpResponse.json(
            apiSuccess({
              results: {},
              errors: { ollama: 'boom' },
            }),
          ),
        ),
      )

      await useSetupWizardStore.getState().probeLocalProviders()

      const state = useSetupWizardStore.getState()
      expect(state.probeResults).toEqual({})
      expect(state.probeErrors['ollama']).toBe('boom')
      expect(state.probeGlobalError).toBeNull()
    })

    it('reprobeLocalProviders clears prior results before re-running', async () => {
      await seedPresets()

      // First run: failure populates probeErrors.
      server.use(
        http.post('/api/v1/providers/probe-local', () =>
          HttpResponse.json(
            apiSuccess({ results: {}, errors: { ollama: 'down' } }),
          ),
        ),
      )
      await useSetupWizardStore.getState().probeLocalProviders()
      expect(useSetupWizardStore.getState().probeErrors).toHaveProperty('ollama')

      // Second run: success -> errors reset.
      server.use(
        http.post('/api/v1/providers/probe-local', () =>
          HttpResponse.json(apiSuccess({ results: {}, errors: {} })),
        ),
      )
      await useSetupWizardStore.getState().reprobeLocalProviders()
      expect(useSetupWizardStore.getState().probeErrors).toEqual({})
      expect(useSetupWizardStore.getState().probeGlobalError).toBeNull()
    })

    it('top-level network failure surfaces probeGlobalError', async () => {
      await seedPresets()
      server.use(
        http.post('/api/v1/providers/probe-local', () =>
          HttpResponse.json(apiError('rate limited'), { status: 429 }),
        ),
      )

      await useSetupWizardStore.getState().probeLocalProviders()

      const state = useSetupWizardStore.getState()
      expect(state.probeGlobalError).not.toBeNull()
      expect(state.probing).toBe(false)
    })
  })
})
