import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import type { issueSteering } from '@/api/endpoints/steering'
import { successFor } from '@/mocks/handlers/helpers'
import { Steering } from '@/pages/mission-control/Steering'
import { useSteeringStore } from '@/stores/steering'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

afterEach(() => {
  useToastStore.getState().dismissAll()
  useSteeringStore.setState({
    directives: [],
    directivesProject: null,
    directivesLoading: false,
    directivesError: null,
    pendingProposal: null,
  })
})

describe('Steering panel', () => {
  it('loads active directives for the seeded project', async () => {
    render(<Steering initialProjectId="checkout" />)
    expect(await screen.findByText('use Postgres not Mongo')).toBeInTheDocument()
    expect(screen.getByText('Issue a directive')).toBeInTheDocument()
  })

  it('hides the issue form until a project is named', () => {
    render(<Steering initialProjectId={null} />)
    expect(screen.queryByText('Issue a directive')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Project')).toBeInTheDocument()
  })

  it('issues a directive and emits a success toast', async () => {
    render(<Steering initialProjectId="checkout" />)
    fireEvent.change(await screen.findByLabelText('Directive'), {
      target: { value: 'pivot off the frontend' },
    })
    fireEvent.click(screen.getByRole('button', { name: /issue directive/i }))
    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.variant === 'success'),
      ).toBe(true)
    })
  })

  it('surfaces a PROPOSE review block with the proposed task set', async () => {
    server.use(
      http.post('/api/v1/cockpit/steering', () =>
        HttpResponse.json(
          successFor<typeof issueSteering>({
            directive_id: 'directive-2',
            kind: 'redirect',
            superseded_task_ids: [],
            proposal: {
              directive_id: 'directive-2',
              proposed_task_ids: ['t1', 't2'],
              rationale: 'these tasks build the old path',
            },
          }),
        ),
      ),
    )
    render(<Steering initialProjectId="checkout" />)
    fireEvent.change(await screen.findByLabelText('Directive'), {
      target: { value: 'pivot off Mongo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /issue directive/i }))
    expect(
      await screen.findByText('Review proposed supersession'),
    ).toBeInTheDocument()
    expect(screen.getByText('these tasks build the old path')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /confirm supersession/i }),
    ).toBeInTheDocument()
  })
})
