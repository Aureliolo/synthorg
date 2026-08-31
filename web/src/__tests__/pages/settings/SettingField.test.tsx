import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SettingDefinition } from '@/api/types/settings'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'
import { SettingField } from '@/pages/settings/SettingField'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderWithName } from '@/utils/providers'
import {
  BUILTIN_EMBEDDER_HINT,
  BUILTIN_EMBEDDER_LABEL,
  BUILTIN_EMBEDDER_MODEL,
  BUILTIN_EMBEDDER_PROVIDER,
} from '@/utils/builtin-embedder'

const EMBEDDER_REF = JSON.stringify({
  provider: BUILTIN_EMBEDDER_PROVIDER,
  model_id: BUILTIN_EMBEDDER_MODEL,
})
const SERVED_REF = JSON.stringify({
  provider: 'test-provider',
  model_id: 'example-capable-001',
})

function makeModel(overrides: Partial<ProviderModelConfig> = {}): ProviderModelConfig {
  return {
    id: 'example-capable-001',
    alias: null,
    capability_overrides: null,
    max_context: 8192,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    estimated_latency_ms: null,
    local_params: null,
    metadata: {
      supports_tools: false,
      supports_vision: false,
      supports_reasoning: false,
      supports_embeddings: true,
      supports_image_generation: false,
      supports_prompt_caching: false,
      max_output_tokens: null,
      parameter_count: null,
      cost_tier: null,
      family: null,
      generation: null,
      release_date: null,
      tool_calls_verified: null,
      metadata_source: 'unknown',
    },
    stale: null,
    ...overrides,
  }
}

function makeProvider(overrides: Partial<ProviderConfig> = {}): ProviderWithName {
  return {
    driver: 'test-provider',
    litellm_provider: null,
    auth_type: 'api_key',
    agent_eligible: true,
    billing_model: 'per_token',
    base_url: null,
    keep_alive: null,
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
    name: 'test-provider',
  }
}

function makeModelRefDefinition(
  overrides: Partial<SettingDefinition> = {},
): SettingDefinition {
  return {
    namespace: 'memory',
    key: 'embedder_model',
    type: 'model_ref',
    default: '',
    description: 'Embedding model',
    group: 'Memory',
    level: 'basic',
    sensitive: false,
    compose_set: false,
    env_var_override: null,
    enum_values: [],
    validator_pattern: null,
    min_value: null,
    max_value: null,
    ...overrides,
  }
}

function renderField(
  definition: SettingDefinition,
  value: string,
  onChange: (next: string) => void = () => {},
) {
  return render(
    <SettingField definition={definition} value={value} onChange={onChange} />,
  )
}

afterEach(() => {
  // Unmount before emptying the store: the field refetches on an empty
  // catalogue, so resetting a still-mounted tree schedules a load nobody
  // awaits. Seeded per test, reset here because the store is module scope
  // shared across every file in a worker.
  cleanup()
  useProvidersStore.setState({ providers: [], listLoading: false })
})

describe('SettingField: built-in embedder on the memory/embedder_model row', () => {
  it('offers the built-in embedder, which no provider serves', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(makeModelRefDefinition(), SERVED_REF)

    expect(
      screen.getByRole('option', { name: BUILTIN_EMBEDDER_LABEL }),
    ).toBeInTheDocument()
  })

  it('preselects the built-in when it is the persisted binding', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(makeModelRefDefinition(), EMBEDDER_REF)

    expect(screen.getByLabelText('Model')).toHaveValue(
      JSON.stringify({
        provider: BUILTIN_EMBEDDER_PROVIDER,
        modelId: BUILTIN_EMBEDDER_MODEL,
      }),
    )
  })

  it('warns that recall is lexical while the built-in is the binding', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(makeModelRefDefinition(), EMBEDDER_REF)

    expect(screen.getByText(BUILTIN_EMBEDDER_HINT)).toBeInTheDocument()
  })

  it('drops the warning once a served model is the binding', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(makeModelRefDefinition(), SERVED_REF)

    expect(screen.queryByText(BUILTIN_EMBEDDER_HINT)).not.toBeInTheDocument()
  })

  it('writes the built-in back in the stored MODEL_REF spelling', async () => {
    // The option value carries ``modelId`` while the setting stores
    // ``model_id``; selecting has to cross that boundary or the write is
    // rejected by the MODEL_REF validator.
    useProvidersStore.setState({ providers: [makeProvider()] })
    const onChange = vi.fn()
    const user = userEvent.setup()

    renderField(makeModelRefDefinition(), SERVED_REF, onChange)
    await user.selectOptions(screen.getByLabelText('Model'), BUILTIN_EMBEDDER_LABEL)

    expect(onChange).toHaveBeenCalledWith(EMBEDDER_REF)
  })
})

describe('SettingField: other MODEL_REF settings', () => {
  it('does not offer the built-in embedder to a setting a provider must serve', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(
      makeModelRefDefinition({ namespace: 'knowledge', key: 'synthesis_model' }),
      SERVED_REF,
    )

    expect(
      screen.queryByRole('option', { name: BUILTIN_EMBEDDER_LABEL }),
    ).not.toBeInTheDocument()
  })

  it('does not warn when the built-in provider name appears elsewhere', () => {
    useProvidersStore.setState({ providers: [makeProvider()] })

    renderField(
      makeModelRefDefinition({ namespace: 'knowledge', key: 'synthesis_model' }),
      EMBEDDER_REF,
    )

    expect(screen.queryByText(BUILTIN_EMBEDDER_HINT)).not.toBeInTheDocument()
  })
})
