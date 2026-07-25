import { fireEvent, screen, waitFor } from '@testing-library/react'
import { delay, http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { WizardModelSelection } from '@/pages/setup/WizardModelSelection'
import { renderWithRouter } from '@/__tests__/test-utils'
import { apiSuccess } from '@/mocks/handlers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import { server } from '@/test-setup'
import { useToastStore } from '@/stores/toast'
import type { SettingNamespace } from '@/api/types/settings'

const PROVIDER = 'test-provider'

// Per-feature model settings are MODEL_REF: the picker selects, and writes
// back, the serialized provider-bound reference rather than a bare model id.
function ref(modelId: string): string {
  return JSON.stringify({ provider: PROVIDER, model_id: modelId })
}

function candidate(modelId: string) {
  return { provider: PROVIDER, model_id: modelId, ref: ref(modelId) }
}

const RECS = {
  decomposition_recommended: ref('large-model-001'),
  model_ref_candidates: [candidate('large-model-001'), candidate('small-model-001')],
  embedding_recommended: 'embed-large-001',
  embedding_recommended_dims: 4096,
  embedding_candidates: ['embed-large-001', 'embed-small-001'],
  research_recommended: ref('large-model-001'),
  cos_recommended: ref('small-model-001'),
}

function recommendationsHandler() {
  return http.get('/api/v1/setup/model-recommendations', () =>
    HttpResponse.json(apiSuccess(RECS)),
  )
}

function capturePut(): {
  calls: { namespace: string; key: string; value: string }[]
} {
  const calls: { namespace: string; key: string; value: string }[] = []
  server.use(
    http.put('/api/v1/settings/:namespace/:key', async ({ params, request }) => {
      const body = (await request.json()) as { value: string }
      const namespace = String(params['namespace'])
      const key = String(params['key'])
      calls.push({ namespace, key, value: body.value })
      return HttpResponse.json(
        apiSuccess(
          buildSettingEntry({
            value: body.value,
            definition: { namespace: namespace as SettingNamespace, key },
          }),
        ),
      )
    }),
  )
  return { calls }
}

describe('WizardModelSelection', () => {
  it('prefills every per-feature model from the recommendations', async () => {
    server.use(recommendationsHandler())
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    expect(
      screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
    ).toBe(ref('large-model-001'))
    // Embedding is a plain string setting, so it stays a bare model id.
    expect(screen.getByLabelText<HTMLSelectElement>('Embedding model').value).toBe(
      'embed-large-001',
    )
    expect(screen.getByLabelText<HTMLSelectElement>('Research model').value).toBe(
      ref('large-model-001'),
    )
    expect(
      screen.getByLabelText<HTMLSelectElement>('Chief of Staff model').value,
    ).toBe(ref('small-model-001'))
    // Embedding is labelled as powering memory + knowledge.
    expect(screen.getByText(/Powers memory \+ knowledge/)).toBeInTheDocument()
  })

  it('labels each candidate with its provider', async () => {
    server.use(recommendationsHandler())
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    // The same model id can be served by more than one provider, so the option
    // text has to name the provider for the choice to be unambiguous.
    expect(
      screen.getAllByRole('option', { name: `large-model-001 (${PROVIDER})` }).length,
    ).toBeGreaterThan(0)
  })

  it('hides the research model picker when research is disabled', async () => {
    server.use(recommendationsHandler())
    renderWithRouter(<WizardModelSelection researchEnabled={false} />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    // The research toggle lives on the Capabilities step; with it off, the
    // research picker is gated out here while the others stay.
    expect(screen.queryByLabelText('Research model')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Embedding model')).toBeInTheDocument()
  })

  it('treats a whitespace-only persisted value as unset', async () => {
    server.use(
      recommendationsHandler(),
      http.get('/api/v1/settings/coordination', () =>
        HttpResponse.json(
          apiSuccess([
            buildSettingEntry({
              value: '   ',
              definition: { namespace: 'coordination', key: 'decomposition_model' },
            }),
          ]),
        ),
      ),
    )
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    // A blank stored value must not win over the recommendation.
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
      ).toBe(ref('large-model-001')),
    )
  })

  it('suppresses the rollback and the error toast for a superseded failed save', async () => {
    let newerSaveDone = false
    server.use(
      http.get('/api/v1/setup/model-recommendations', () =>
        HttpResponse.json(
          apiSuccess({
            ...RECS,
            decomposition_recommended: ref('large-model-001'),
            model_ref_candidates: [
              candidate('large-model-001'),
              candidate('small-model-001'),
              candidate('medium-model-001'),
            ],
          }),
        ),
      ),
      http.put('/api/v1/settings/coordination/decomposition_model', async ({ request }) => {
        const body = (await request.json()) as { value: string }
        // The older write (to small) fails immediately, so its catch runs well
        // before the newer write (to medium, delayed below) resolves.
        if (body.value === ref('small-model-001')) {
          return new HttpResponse(null, { status: 500 })
        }
        await delay(30)
        newerSaveDone = true
        return HttpResponse.json(
          apiSuccess(
            buildSettingEntry({
              value: body.value,
              definition: { namespace: 'coordination', key: 'decomposition_model' },
            }),
          ),
        )
      }),
    )
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    const select = await screen.findByLabelText<HTMLSelectElement>('Coordination model')
    fireEvent.change(select, { target: { value: ref('small-model-001') } }) // older write, fails now
    fireEvent.change(select, { target: { value: ref('medium-model-001') } }) // newer write, succeeds later
    // By the time the newer (delayed) write resolves, the older failure has long
    // since run its catch -- which must have suppressed BOTH the rollback and the
    // toast because a newer write superseded it.
    await waitFor(() => expect(newerSaveDone).toBe(true))
    expect(select.value).toBe(ref('medium-model-001'))
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('prefers a persisted value over the recommendation', async () => {
    server.use(
      recommendationsHandler(),
      http.get('/api/v1/settings/coordination', () =>
        HttpResponse.json(
          apiSuccess([
            buildSettingEntry({
              value: ref('small-model-001'),
              definition: { namespace: 'coordination', key: 'decomposition_model' },
            }),
          ]),
        ),
      ),
    )
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
      ).toBe(ref('small-model-001')),
    )
  })

  it('preselects a persisted ref written in the backend JSON spelling', async () => {
    // ``json.dumps`` pads after ``:`` and ``,`` where ``JSON.stringify`` does
    // not, so the same reference reaches the picker as two distinct strings
    // depending on which side last wrote it. Both must preselect.
    const spaced = '{"provider": "test-provider", "model_id": "small-model-001"}'
    expect(spaced).not.toBe(ref('small-model-001'))
    server.use(
      recommendationsHandler(),
      http.get('/api/v1/settings/coordination', () =>
        HttpResponse.json(
          apiSuccess([
            buildSettingEntry({
              value: spaced,
              definition: { namespace: 'coordination', key: 'decomposition_model' },
            }),
          ]),
        ),
      ),
    )
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
      ).toBe(ref('small-model-001')),
    )
  })

  it('persists a model override through the settings API', async () => {
    server.use(recommendationsHandler())
    const { calls } = capturePut()
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(screen.getByLabelText('Research model')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByLabelText('Research model'), {
      target: { value: ref('small-model-001') },
    })
    await waitFor(() =>
      expect(calls).toContainEqual({
        namespace: 'research',
        key: 'model',
        value: ref('small-model-001'),
      }),
    )
  })

  it('writes a provider-bound reference, never a bare model id', async () => {
    server.use(recommendationsHandler())
    const { calls } = capturePut()
    renderWithRouter(<WizardModelSelection researchEnabled={true} />)
    await waitFor(() =>
      expect(screen.getByLabelText('Concern-routing model')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByLabelText('Concern-routing model'), {
      target: { value: ref('small-model-001') },
    })
    // The MODEL_REF validator rejects any value that is not canonical
    // {provider, model_id} JSON, so a bare id here is a saved-setting failure.
    await waitFor(() => expect(calls).toHaveLength(1))
    const written = calls[0]
    expect(written).toBeDefined()
    expect(JSON.parse(written?.value ?? '')).toEqual({
      provider: PROVIDER,
      model_id: 'small-model-001',
    })
  })
})
