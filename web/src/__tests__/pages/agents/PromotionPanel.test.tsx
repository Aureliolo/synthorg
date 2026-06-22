import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiSuccess, buildPromotionEvaluation } from '@/mocks/handlers'
import { PromotionPanel } from '@/pages/agents/PromotionPanel'
import { usePromotionStore } from '@/stores/promotion'
import { useToastStore } from '@/stores/toast'
import { server } from '@/test-setup'

// Permission gating reads useAuth().userRole; vary it per test through a
// hoisted holder so the promote/demote actions can be shown or hidden.
const authMock = vi.hoisted(() => ({ userRole: 'ceo' }))
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ userRole: authMock.userRole }),
}))

describe('PromotionPanel', () => {
  beforeEach(() => {
    authMock.userRole = 'ceo'
    usePromotionStore.getState().reset()
    useToastStore.getState().dismissAll()
  })

  it('opens the eligibility drawer and renders the evaluation', async () => {
    server.use(
      http.get('/api/v1/promotion/:id/evaluate', () =>
        HttpResponse.json(apiSuccess(buildPromotionEvaluation({ eligible: true }))),
      ),
    )
    const user = userEvent.setup()
    render(<PromotionPanel agentId="agent-1" />)

    await user.click(screen.getByRole('button', { name: /check promotion eligibility/i }))

    const drawer = await screen.findByRole('dialog')
    await within(drawer).findByText(/eligible for promotion/i)
    expect(within(drawer).getByText('tasks_completed')).toBeInTheDocument()
  })

  it('applies a promotion through the confirmation dialog', async () => {
    const user = userEvent.setup()
    render(<PromotionPanel agentId="agent-1" />)

    await user.click(screen.getByRole('button', { name: /^promote$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^promote$/i }))

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.variant === 'success'),
      ).toBe(true)
    })
  })

  it('hides promote/demote actions for roles that cannot manage promotions', () => {
    authMock.userRole = 'developer'
    render(<PromotionPanel agentId="agent-1" />)

    expect(screen.queryByRole('button', { name: /^promote$/i })).not.toBeInTheDocument()
    expect(
      screen.getByText(/only ceo and manager roles can promote or demote/i),
    ).toBeInTheDocument()
  })
})
