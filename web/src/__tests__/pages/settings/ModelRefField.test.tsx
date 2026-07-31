import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { ModelRefField } from '@/pages/settings/ModelRefField'
import { useProvidersStore } from '@/stores/providers'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { probeEmbedder } from '@/api/endpoints/memory'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

/**
 * An embedder's vector width decides whether recall can be indexed at all,
 * and only the model can answer it. Surfacing it at the moment of choosing is
 * what stops a good model being picked, saved, restarted into, and only then
 * revealing that it disabled the index.
 */

function model(
  id: string,
  overrides: Partial<ProviderModelConfig['metadata']> = {},
): ProviderModelConfig {
  return {
    id,
    alias: null,
    max_context: 8192,
    stale: null,
    metadata: {
      family: 'test-family',
      supports_tools: false,
      supports_vision: false,
      supports_embeddings: false,
      supports_image_generation: false,
      tool_calls_verified: null,
      metadata_source: 'preset',
      ...overrides,
    },
  } as ProviderModelConfig
}

function seedProviders(): void {
  const provider = {
    name: 'example-provider',
    models: [
      model('example-chat-001'),
      model('example-embed-001', { supports_embeddings: true }),
    ],
  } as ProviderConfig
  useProvidersStore.setState({ providers: [provider], listLoading: false })
}

function renderEmbedderField() {
  seedProviders()
  return render(
    <ModelRefField
      value=""
      onChange={() => undefined}
      settingKey="memory/embedder_model"
    />,
  )
}

describe('ModelRefField embedder width', () => {
  it('offers only embedding models for the embedder setting', async () => {
    // A chat model cannot produce an embedding, so listing one here offers a
    // choice that can only fail at dispatch.
    renderEmbedderField()

    const options = await screen.findAllByRole('option')
    const labels = options.map((o) => o.textContent ?? '')
    expect(labels.some((l) => l.includes('example-embed-001'))).toBe(true)
    expect(labels.some((l) => l.includes('example-chat-001'))).toBe(false)
  })

  it('reports the measured width and that it will be indexed', async () => {
    renderEmbedderField()

    await userEvent.selectOptions(
      await screen.findByRole('combobox'),
      JSON.stringify({ provider: 'example-provider', modelId: 'example-embed-001' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/1024 dimensions: indexed/)).toBeInTheDocument()
    })
  })

  it('says plainly when a width is too wide to index', async () => {
    // The case that cost the operator a restart to discover: correct recall,
    // no index, every search reading the whole corpus.
    server.use(
      http.post('/api/v1/admin/memory/embedder/probe', () =>
        HttpResponse.json(
          successFor<typeof probeEmbedder>({
            dims: 4096,
            index_support: 'exact_scan',
            vector_ceiling: 2000,
            halfvec_ceiling: 4000,
          }),
        ),
      ),
    )
    renderEmbedderField()

    await userEvent.selectOptions(
      await screen.findByRole('combobox'),
      JSON.stringify({ provider: 'example-provider', modelId: 'example-embed-001' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/too wide to index/)).toBeInTheDocument()
    })
    expect(screen.getByText(/every search reads every stored memory/)).toBeInTheDocument()
  })
})
