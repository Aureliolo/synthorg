import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { ModelRefField } from '@/pages/settings/ModelRefField'
import { useProvidersStore } from '@/stores/providers'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { probeEmbedder } from '@/api/endpoints/memory'
import type { ProviderModelConfig } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

/**
 * An embedder's vector width decides whether recall can be indexed at all,
 * and only the model can answer it. Surfacing it at the moment of choosing is
 * what stops a good model being picked, saved, restarted into, and only then
 * revealing that it disabled the index.
 */

function model(id: string, supportsEmbeddings: boolean): ProviderModelConfig {
  return {
    id,
    alias: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    max_context: 8192,
    estimated_latency_ms: null,
    local_params: null,
    metadata: {
      supports_tools: false,
      tool_calls_verified: null,
      supports_vision: false,
      supports_reasoning: false,
      supports_embeddings: supportsEmbeddings,
      supports_image_generation: false,
      supports_prompt_caching: false,
      max_output_tokens: null,
      parameter_count: null,
      cost_tier: null,
      family: 'test-family',
      generation: null,
      release_date: null,
      metadata_source: 'unknown',
    },
    stale: null,
  }
}

function seedProviders(): void {
  const provider = {
    name: 'example-provider',
    models: [model('example-chat-001', false), model('example-embed-001', true)],
  } as unknown as ProviderWithName
  useProvidersStore.setState({ providers: [provider], listLoading: false })
}

/**
 * Host the field the way the settings page does: controlled, so a selection
 * arrives back through `value`. Pinning `value` while accepting `onChange`
 * would model a parent that drops every edit, which no caller does and which
 * hides whether the field reads its own value at all.
 */
function EmbedderFieldHost({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <ModelRefField
        value={value}
        onChange={setValue}
        settingKey="memory/embedder_model"
      />
      <button type="button" onClick={() => setValue(initial)}>
        Discard
      </button>
    </>
  )
}

function renderEmbedderField() {
  seedProviders()
  return render(<EmbedderFieldHost />)
}

describe('ModelRefField embedder width', () => {
  it('offers only embedding models for the embedder setting', async () => {
    // A chat model cannot produce an embedding, so listing one here offers a
    // choice that can only fail at dispatch.
    renderEmbedderField()

    const options = await screen.findAllByRole('option')
    const labels = options.map((o) => o.textContent)
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

  it('drops the verdict when the value is reset out from under it', async () => {
    // Discarding edits resets the row to its persisted value without going
    // through the picker. A width measured on the model being discarded says
    // nothing about the one that comes back.
    renderEmbedderField()

    await userEvent.selectOptions(
      await screen.findByRole('combobox'),
      JSON.stringify({ provider: 'example-provider', modelId: 'example-embed-001' }),
    )
    await waitFor(() => {
      expect(screen.getByText(/1024 dimensions: indexed/)).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: 'Discard' }))

    await waitFor(() => {
      expect(screen.queryByText(/1024 dimensions: indexed/)).toBeNull()
    })
  })
})
