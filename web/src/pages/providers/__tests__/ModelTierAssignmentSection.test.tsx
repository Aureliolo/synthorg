import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { TierAssignmentsResponse } from '@/api/types/providers'
import { ModelTierAssignmentSection } from '../ModelTierAssignmentSection'

const BASE = '/api/v1/providers/tier-assignments'

async function enableRecommender(): Promise<void> {
  fireEvent.change(screen.getByLabelText('Classifier model'), {
    target: { value: 'local-host␟tiny-7b' },
  })
  await waitFor(() =>
    expect(screen.getByRole('switch', { name: 'Enable LLM recommender' })).toBeEnabled(),
  )
  fireEvent.click(screen.getByRole('switch', { name: 'Enable LLM recommender' }))
}

describe('ModelTierAssignmentSection', () => {
  it('renders configured models with their tier and provenance', async () => {
    render(<ModelTierAssignmentSection />)
    expect(await screen.findByText('tiny-7b')).toBeInTheDocument()
    expect(screen.getByText('huge-120b')).toBeInTheDocument()
    expect(screen.getByText(/Operator ·/)).toBeInTheDocument()
  })

  it('disables the recommend actions until the recommender is enabled', async () => {
    render(<ModelTierAssignmentSection />)
    await screen.findByText('tiny-7b')
    expect(screen.getByRole('button', { name: 'Recommend all fresh' })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Recommend a tier for tiny-7b' }),
    ).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Classifier model'), {
      target: { value: 'local-host␟tiny-7b' },
    })
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Enable LLM recommender' })).toBeEnabled(),
    )
    expect(
      screen.getByRole('button', { name: 'Recommend a tier for tiny-7b' }),
    ).toBeDisabled()
  })

  it('enables and runs the LLM recommender once a model is picked and opt-in is on', async () => {
    render(<ModelTierAssignmentSection />)
    await screen.findByText('tiny-7b')

    await enableRecommender()

    const recommend = await screen.findByRole('button', {
      name: 'Recommend a tier for tiny-7b',
    })
    await waitFor(() => expect(recommend).toBeEnabled())
    fireEvent.click(recommend)

    expect(
      await screen.findByRole('button', {
        name: /Apply the Small tier recommendation for tiny-7b/,
      }),
    ).toBeInTheDocument()
  })

  it('overrides a tier and reflects the new tier on the row', async () => {
    const overridden: TierAssignmentsResponse = {
      assignments: [
        {
          provider: 'local-host',
          model_id: 'tiny-7b',
          tier: 'large',
          provenance: 'operator',
          confidence: 1,
          reason: 'operator override',
          is_override: true,
        },
      ],
    }
    server.use(
      http.put(`${BASE}/:provider/:modelId`, () =>
        HttpResponse.json(apiSuccess(overridden)),
      ),
    )
    render(<ModelTierAssignmentSection />)
    const modelCell = await screen.findByText('tiny-7b')
    const select = within(modelCell.closest('tr') as HTMLElement).getByLabelText(
      'Override tier for tiny-7b',
    )
    fireEvent.change(select, { target: { value: 'large' } })

    await waitFor(() => {
      const row = screen.getByText('tiny-7b').closest('tr') as HTMLElement
      expect(within(row).getByLabelText('Override tier for tiny-7b')).toHaveValue('large')
      expect(within(row).getByText(/Operator ·/)).toBeInTheDocument()
    })
  })

  it('shows an error banner when the effective map cannot load', async () => {
    server.use(
      http.get(BASE, () => HttpResponse.json(apiError('tier boom'), { status: 500 })),
    )
    render(<ModelTierAssignmentSection />)
    expect(await screen.findByText('Could not load tier assignments')).toBeInTheDocument()
  })
})
