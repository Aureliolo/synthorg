import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiError, apiSuccess } from '@/mocks/handlers'
import { MetaChat } from '@/pages/meta/MetaChat'
import { useMetaStore } from '@/stores/meta'
import { server } from '@/test-setup'

beforeEach(() => {
  useMetaStore.setState({ chatLoading: false, error: null })
})

describe('MetaChat', () => {
  it('renders the empty state before any message', () => {
    render(<MetaChat />)
    expect(screen.getByText('Ask the Chief of Staff')).toBeInTheDocument()
  })

  it('renders the question then the answer after sending', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(
          apiSuccess({
            answer: 'Signals look healthy this week.',
            sources: ['signal:revenue'],
            confidence: 0.9,
          }),
        ),
      ),
    )
    const user = userEvent.setup()
    render(<MetaChat />)

    await user.type(screen.getByLabelText('Chat message'), 'how are signals?')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(
        screen.getByText('Signals look healthy this week.'),
      ).toBeInTheDocument()
    })
    expect(screen.getByText('how are signals?')).toBeInTheDocument()
  })

  it('renders a failure notice when the chat request fails', async () => {
    server.use(
      http.post('/api/v1/meta/chat', () =>
        HttpResponse.json(apiError('boom')),
      ),
    )
    const user = userEvent.setup()
    render(<MetaChat />)

    await user.type(screen.getByLabelText('Chat message'), 'anything')
    await user.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByText(/Chat request failed/)).toBeInTheDocument()
    })
  })
})
