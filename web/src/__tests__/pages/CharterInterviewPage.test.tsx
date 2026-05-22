import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import CharterInterviewPage from '@/pages/CharterInterviewPage'
import { useCharterStore } from '@/stores/charter'
import { useToastStore } from '@/stores/toast'

function renderPage() {
  return render(
    <MemoryRouter>
      <CharterInterviewPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useCharterStore.getState().resetInterview()
  useCharterStore.setState({ charters: [], loading: false, error: null })
  useToastStore.getState().dismissAll()
})

describe('CharterInterviewPage', () => {
  it('shows the empty draft state before any interview turn', () => {
    renderPage()
    expect(screen.getByText('No charter yet')).toBeInTheDocument()
  })

  it('drives a turn and renders the drafted charter for review', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.type(
      screen.getByLabelText('Your message'),
      'build a better memory layer',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    // Default MSW interview handler returns a drafted charter.
    await waitFor(() => {
      expect(screen.getByText('Better memory layer')).toBeInTheDocument()
    })
    expect(
      screen.getByRole('button', { name: /approve & start run/i }),
    ).toBeInTheDocument()
  })

  it('approves the drafted charter', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.type(screen.getByLabelText('Your message'), 'a clear idea')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => {
      expect(screen.getByText('Better memory layer')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /approve & start run/i }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts[0]?.variant).toBe('success')
    })
  })
})
