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

const RECS = {
  decomposition_recommended: 'large-model-001',
  decomposition_candidates: ['large-model-001', 'small-model-001'],
  embedding_recommended: 'embed-large-001',
  embedding_recommended_dims: 4096,
  embedding_candidates: ['embed-large-001', 'embed-small-001'],
  research_recommended: 'large-model-001',
  cos_recommended: 'small-model-001',
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
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
    )
    expect(
      screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
    ).toBe('large-model-001')
    expect(screen.getByLabelText<HTMLSelectElement>('Embedding model').value).toBe(
      'embed-large-001',
    )
    expect(screen.getByLabelText<HTMLSelectElement>('Research model').value).toBe(
      'large-model-001',
    )
    expect(
      screen.getByLabelText<HTMLSelectElement>('Chief of Staff model').value,
    ).toBe('small-model-001')
    // Embedding is labelled as powering memory + knowledge.
    expect(screen.getByText(/Powers memory \+ knowledge/)).toBeInTheDocument()
  })

  it('defaults the research + knowledge toggles to on', async () => {
    server.use(recommendationsHandler())
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Research' })).toBeInTheDocument(),
    )
    expect(screen.getByRole('switch', { name: 'Research' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByRole('switch', { name: 'Knowledge base' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
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
    renderWithRouter(<WizardModelSelection />)
    // A blank stored value must not win over the recommendation.
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>('Coordination model').value,
      ).toBe('large-model-001'),
    )
  })

  it('keeps the newest model choice when an older write fails late', async () => {
    server.use(
      http.get('/api/v1/setup/model-recommendations', () =>
        HttpResponse.json(
          apiSuccess({
            ...RECS,
            decomposition_recommended: 'large-model-001',
            decomposition_candidates: [
              'large-model-001',
              'small-model-001',
              'medium-model-001',
            ],
          }),
        ),
      ),
      http.put('/api/v1/settings/coordination/decomposition_model', async ({ request }) => {
        const body = (await request.json()) as { value: string }
        // The older write (to small) fails, and does so *after* the newer write
        // (to medium) has already succeeded -- the exact rollback-race ordering.
        if (body.value === 'small-model-001') {
          await delay(50)
          return new HttpResponse(null, { status: 500 })
        }
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
    renderWithRouter(<WizardModelSelection />)
    const select = await screen.findByLabelText<HTMLSelectElement>('Coordination model')
    fireEvent.change(select, { target: { value: 'small-model-001' } })
    fireEvent.change(select, { target: { value: 'medium-model-001' } })
    await waitFor(() => expect(select.value).toBe('medium-model-001'))
    // Wait for the older write's delayed failure to surface (its error toast is
    // added to the store), which both drains the timer and proves the rollback
    // path ran: the guard must keep the newer value, not the pre-edit one.
    await waitFor(() =>
      expect(useToastStore.getState().toasts.length).toBeGreaterThan(0),
    )
    expect(select.value).toBe('medium-model-001')
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

  it('persists a model override through the settings API', async () => {
    server.use(recommendationsHandler())
    const { calls } = capturePut()
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText('Research model')).toBeInTheDocument(),
    )
    fireEvent.change(screen.getByLabelText('Research model'), {
      target: { value: 'small-model-001' },
    })
    await waitFor(() =>
      expect(calls).toContainEqual({
        namespace: 'research',
        key: 'model',
        value: 'small-model-001',
      }),
    )
  })

  it('disabling research persists the flag and hides the research model', async () => {
    server.use(recommendationsHandler())
    const { calls } = capturePut()
    renderWithRouter(<WizardModelSelection />)
    await waitFor(() =>
      expect(screen.getByLabelText('Research model')).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Research' }))
    await waitFor(() =>
      expect(calls).toContainEqual({
        namespace: 'research',
        key: 'enabled',
        value: 'false',
      }),
    )
    expect(screen.queryByLabelText('Research model')).not.toBeInTheDocument()
  })
})
