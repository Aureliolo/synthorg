import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { OrgEventCard } from '@/pages/chat/org-chat-events'
import type { SecretCaptureEvent } from '@/pages/chat/org-chat-types'

const CAPTURE_EVENT: SecretCaptureEvent = {
  type: 'secret-capture',
  draftId: 'draft-9',
  captures: [
    {
      connectionType: 'database',
      fieldName: 'password',
      secretKind: 'password',
      label: 'Password',
    },
  ],
}

function renderCard(onSubmit: (
  turnId: number,
  draftId: string,
  handles: Readonly<Record<string, string>>,
) => void) {
  render(
    <OrgEventCard
      turnId={1}
      event={CAPTURE_EVENT}
      resolvingInvites={new Set()}
      onResolveInvite={vi.fn()}
      sending={false}
      onSubmitSecretCaptures={onSubmit}
    />,
  )
}

describe('SecretCaptureCard', () => {
  it('captures the raw value out of band and passes only the handle to the turn', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    renderCard(onSubmit)

    const secret = 'ghp_supersecretsentinel00000000000000'
    await user.type(screen.getByLabelText('Password'), secret)
    await user.click(screen.getByRole('button', { name: /provide securely/i }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const [turnId, draftId, handles] = onSubmit.mock.calls[0] as [
      number,
      string,
      Record<string, string>,
    ]
    expect(turnId).toBe(1)
    expect(draftId).toBe('draft-9')
    // The MSW capture endpoint hands back an opaque handle; the raw secret must
    // never be what the turn receives.
    expect(handles).toEqual({ password: 'sech_mock_handle_0001' })
    expect(JSON.stringify(handles)).not.toContain(secret)
  })

  it('disables the submit until every field has a value', () => {
    renderCard(vi.fn())
    expect(screen.getByRole('button', { name: /provide securely/i })).toBeDisabled()
  })

  it('shows a submitted state once resolved', () => {
    render(
      <OrgEventCard
        turnId={1}
        event={{ ...CAPTURE_EVENT, resolved: 'submitted' }}
        resolvingInvites={new Set()}
        onResolveInvite={vi.fn()}
        sending={false}
        onSubmitSecretCaptures={vi.fn()}
      />,
    )
    expect(screen.getByText(/captured securely/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /provide securely/i }),
    ).not.toBeInTheDocument()
  })
})
