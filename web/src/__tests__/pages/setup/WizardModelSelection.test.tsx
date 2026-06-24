import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

const getModelRecommendations = vi.fn<() => Promise<unknown>>()
const getNamespaceSettings = vi.fn<(ns: string) => Promise<unknown>>()
const updateSetting = vi.fn<(ns: string, key: string, data: unknown) => Promise<unknown>>()

vi.mock('@/api/endpoints/setup', () => ({
  getModelRecommendations: () => getModelRecommendations(),
}))
vi.mock('@/api/endpoints/settings', () => ({
  getNamespaceSettings: (ns: string) => getNamespaceSettings(ns),
  updateSetting: (ns: string, key: string, data: unknown) => updateSetting(ns, key, data),
}))

// Import after the mocks so the component binds to the stubbed endpoints.
const { WizardModelSelection } = await import('@/pages/setup/WizardModelSelection')

const RECS = {
  decomposition_recommended: 'big-model',
  decomposition_candidates: ['big-model', 'small-model'],
  embedding_recommended: 'qwen3-embedding:8b',
  embedding_recommended_dims: 4096,
  embedding_candidates: ['qwen3-embedding:8b', 'nomic-embed-text'],
}

describe('WizardModelSelection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getNamespaceSettings.mockResolvedValue([])
    updateSetting.mockResolvedValue({})
    getModelRecommendations.mockResolvedValue(RECS)
  })

  it('prefills the recommended models and the dims hint', async () => {
    render(<WizardModelSelection />)
    await waitFor(() => expect(screen.getByLabelText('Coordination model')).toBeInTheDocument())
    expect(screen.getByLabelText<HTMLSelectElement>('Coordination model').value).toBe('big-model')
    expect(screen.getByLabelText<HTMLSelectElement>('Embedding model').value).toBe(
      'qwen3-embedding:8b',
    )
    expect(screen.getByText(/4096 dimensions/)).toBeInTheDocument()
  })

  it('prefers a persisted value over the recommendation', async () => {
    getNamespaceSettings.mockImplementation((ns: string) =>
      Promise.resolve(
        ns === 'coordination'
          ? [{ definition: { key: 'decomposition_model' }, value: 'small-model' }]
          : [],
      ),
    )
    render(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText<HTMLSelectElement>('Coordination model').value).toBe(
        'small-model',
      ),
    )
  })

  it('persists an override through the settings API', async () => {
    render(<WizardModelSelection />)
    await waitFor(() => expect(screen.getByLabelText('Coordination model')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Coordination model'), {
      target: { value: 'small-model' },
    })
    await waitFor(() =>
      expect(updateSetting).toHaveBeenCalledWith('coordination', 'decomposition_model', {
        value: 'small-model',
      }),
    )
  })
})
