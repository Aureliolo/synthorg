import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { ModelTierAssignmentSection } from '../ModelTierAssignmentSection'

const BASE = '/api/v1/providers/tier-assignments'

describe('ModelTierAssignmentSection', () => {
  it('renders configured models with their tier and provenance', async () => {
    render(<ModelTierAssignmentSection />)
    expect(await screen.findByText('tiny-7b')).toBeInTheDocument()
    expect(screen.getByText('huge-120b')).toBeInTheDocument()
    // Provenance pill carries the label + confidence.
    expect(screen.getByText(/Operator ·/)).toBeInTheDocument()
  })

  it('disables the recommend actions until a classifier model is picked', async () => {
    render(<ModelTierAssignmentSection />)
    await screen.findByText('tiny-7b')
    expect(screen.getByRole('button', { name: 'Recommend all fresh' })).toBeDisabled()
    for (const button of screen.getAllByRole('button', { name: 'Recommend' })) {
      expect(button).toBeDisabled()
    }
  })

  it('enables and runs the LLM recommender once a classifier is chosen', async () => {
    render(<ModelTierAssignmentSection />)
    await screen.findByText('tiny-7b')

    fireEvent.change(screen.getByLabelText('Classifier model'), {
      target: { value: 'local-host␟tiny-7b' },
    })

    // The row recommend buttons enable once the classifier PUT resolves.
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Recommend' })[0]).toBeEnabled(),
    )
    fireEvent.click(screen.getAllByRole('button', { name: 'Recommend' })[0] as HTMLElement)

    expect(await screen.findByRole('button', { name: 'Apply' })).toBeInTheDocument()
  })

  it('overrides a tier through the row select', async () => {
    render(<ModelTierAssignmentSection />)
    const modelCell = await screen.findByText('tiny-7b')
    const row = modelCell.closest('tr')
    expect(row).not.toBeNull()
    const select = within(row as HTMLElement).getByLabelText('Override tier for tiny-7b')
    fireEvent.change(select, { target: { value: 'large' } })
    // The default handler returns the full map; the operator-provenance row
    // stays present after the write resolves.
    expect(await screen.findByText('huge-120b')).toBeInTheDocument()
  })

  it('shows an error banner when the effective map cannot load', async () => {
    server.use(
      http.get(BASE, () => HttpResponse.json(apiError('tier boom'), { status: 500 })),
    )
    render(<ModelTierAssignmentSection />)
    expect(await screen.findByText('Could not load tier assignments')).toBeInTheDocument()
  })
})
