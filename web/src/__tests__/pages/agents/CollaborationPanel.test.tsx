import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useToastStore } from '@/stores/toast'
import { CollaborationPanel } from '@/pages/agents/CollaborationPanel'

// Permission gating reads useAuth().userRole; vary it per test through a
// hoisted holder so the clear action can be shown or hidden.
const authMock = vi.hoisted(() => ({ userRole: 'ceo' }))
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ userRole: authMock.userRole }),
}))

describe('CollaborationPanel', () => {
  beforeEach(() => {
    authMock.userRole = 'ceo'
    useToastStore.getState().dismissAll()
  })

  it('clears the override through a confirmation dialog', async () => {
    const user = userEvent.setup()
    render(<CollaborationPanel agentId="agent-1" />)

    await screen.findByText('Collaboration Override')
    await user.click(
      screen.getByRole('button', { name: /clear collaboration override/i }),
    )

    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /clear override/i }))

    await waitFor(() => {
      expect(
        useToastStore
          .getState()
          .toasts.some((t) => t.title === 'Collaboration override cleared'),
      ).toBe(true)
    })
  })

  it('hides the clear action for roles that cannot manage overrides', async () => {
    authMock.userRole = 'developer'
    render(<CollaborationPanel agentId="agent-1" />)

    await screen.findByText('Collaboration Override')
    expect(
      screen.queryByRole('button', { name: /clear collaboration override/i }),
    ).not.toBeInTheDocument()
  })
})
