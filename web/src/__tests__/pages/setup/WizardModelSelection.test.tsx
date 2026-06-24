import { fireEvent, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { WizardModelSelection } from '@/pages/setup/WizardModelSelection'
import { renderWithRouter } from '@/__tests__/test-utils'
import { apiSuccess } from '@/mocks/handlers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import { server } from '@/test-setup'

const RECS = {
  decomposition_recommended: 'large-model-001',
  decomposition_candidates: ['large-model-001', 'small-model-001'],
  embedding_recommended: 'embed-large-001',
  embedding_recommended_dims: 4096,
  embedding_candidates: ['embed-large-001', 'embed-small-001'],
}

function recommendationsHandler() {
  return http.get('/api/v1/setup/model-recommendations', () =>
    HttpResponse.json(apiSuccess(RECS)),
  )
}

describe('WizardModelSelection', () => {
  it('prefills the recommended models and the dims hint', async () => {
    server.use(recommendationsHandler())
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    expect(
      screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
    ).toBe('large-model-001')
    expect(
      screen.getByLabelText<HTMLSelectElement>('Embedding model').value,
    ).toBe('embed-large-001')
    expect(screen.getByText(/4096 dimensions/)).toBeInTheDocument()
  })

  it('prefers a persisted value over the recommendation', async () => {
    server.use(
      recommendationsHandler(),
      http.get('/api/v1/settings/coordination', () =>
        HttpResponse.json(
          apiSuccess([
            buildSettingEntry({
              value: 'small-model-001',
              definition: { namespace: 'coordination', key: 'decomposition_model' },
            }),
          ]),
        ),
      ),
    )
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
      ).toBe('small-model-001'),
    )
  })

  it('persists an override through the settings API', async () => {
    let lastPut: { namespace: string; key: string; value: string } | null = null
    server.use(
      recommendationsHandler(),
      http.put('/api/v1/settings/:namespace/:key', async ({ params, request }) => {
        const body = (await request.json()) as { value: string }
        lastPut = {
          namespace: String(params['namespace']),
          key: String(params['key']),
          value: body.value,
        }
        return HttpResponse.json(
          apiSuccess(
            buildSettingEntry({
              value: body.value,
              definition: {
                namespace: 'coordination',
                key: String(params['key']),
              },
            }),
          ),
        )
      }),
    )
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByLabelText('Coordination model'), {
      target: { value: 'small-model-001' },
    })
    await waitFor(() =>
      expect(lastPut).toEqual({
        namespace: 'coordination',
        key: 'decomposition_model',
        value: 'small-model-001',
      }),
    )
  })
})
