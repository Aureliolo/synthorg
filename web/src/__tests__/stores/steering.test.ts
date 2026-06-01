import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { successFor } from '@/mocks/handlers/helpers'
import { apiError } from '@/mocks/handlers'
import { useSteeringStore } from '@/stores/steering'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'
import type { issueSteering } from '@/api/endpoints/steering'

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

function hasToast(variant: 'success' | 'error'): boolean {
  return useToastStore.getState().toasts.some((t) => t.variant === variant)
}

describe('useSteeringStore', () => {
  it('fetchDirectives stores the active directives', async () => {
    await useSteeringStore.getState().fetchDirectives('checkout')
    const state = useSteeringStore.getState()
    expect(state.directives).toHaveLength(1)
    expect(state.directives[0]?.text).toBe('use Postgres not Mongo')
    expect(state.directivesError).toBeNull()
  })

  it('fetchDirectives sets error and clears directives on failure', async () => {
    server.use(
      http.get('/api/v1/cockpit/steering', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    await useSteeringStore.getState().fetchDirectives('checkout')
    const state = useSteeringStore.getState()
    expect(state.directives).toEqual([])
    expect(state.directivesError).not.toBeNull()
  })

  it('issueDirective toasts success and refreshes the directive list', async () => {
    const result = await useSteeringStore.getState().issueDirective({
      project_id: 'checkout',
      kind: 'redirect',
      text: 'use Postgres not Mongo',
    })
    expect(result?.directive_id).toBe('directive-1')
    expect(hasToast('success')).toBe(true)
    // The happy-path issue has no proposal; PROPOSE flow is exercised below.
    expect(useSteeringStore.getState().pendingProposal).toBeNull()
    // Directives were refreshed from the list endpoint after issuing.
    expect(useSteeringStore.getState().directives).toHaveLength(1)
  })

  it('issueDirective surfaces a PROPOSE proposal for review', async () => {
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
              rationale: 'these tasks build the old Mongo path',
            },
          }),
        ),
      ),
    )
    await useSteeringStore.getState().issueDirective({
      project_id: 'checkout',
      kind: 'redirect',
      text: 'pivot off Mongo',
      supersede_mode: 'propose',
    })
    const proposal = useSteeringStore.getState().pendingProposal
    expect(proposal?.proposed_task_ids).toEqual(['t1', 't2'])
  })

  it('issueDirective returns null and toasts on error', async () => {
    server.use(
      http.post('/api/v1/cockpit/steering', () =>
        HttpResponse.json(apiError('boom'), { status: 500 }),
      ),
    )
    const result = await useSteeringStore.getState().issueDirective({
      project_id: 'checkout',
      kind: 'hint',
      text: 'prefer the util',
    })
    expect(result).toBeNull()
    expect(hasToast('error')).toBe(true)
  })

  it('confirmSupersession cancels the edited set and clears the proposal', async () => {
    useSteeringStore.setState({
      pendingProposal: {
        directive_id: 'directive-2',
        proposed_task_ids: ['t1', 't2'],
        rationale: 'old path',
      },
    })
    const result = await useSteeringStore
      .getState()
      .confirmSupersession('directive-2', 'checkout', ['t1'])
    expect(result?.cancelled_task_ids).toEqual(['t1'])
    expect(useSteeringStore.getState().pendingProposal).toBeNull()
    expect(hasToast('success')).toBe(true)
  })

  it('dismissProposal clears a pending proposal', () => {
    useSteeringStore.setState({
      pendingProposal: {
        directive_id: 'directive-2',
        proposed_task_ids: ['t1'],
        rationale: 'old path',
      },
    })
    useSteeringStore.getState().dismissProposal()
    expect(useSteeringStore.getState().pendingProposal).toBeNull()
  })
})
