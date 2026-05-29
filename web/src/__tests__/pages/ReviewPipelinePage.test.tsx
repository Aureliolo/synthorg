import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { renderRoutes } from '@/__tests__/test-utils'
import ReviewPipelinePage from '@/pages/ReviewPipelinePage'

function renderPage(taskId = 'task-1') {
  return renderRoutes([{ path: '/review/:taskId', element: <ReviewPipelinePage /> }], {
    initialEntries: [`/review/${taskId}`],
  })
}

describe('ReviewPipelinePage', () => {
  it('renders the pipeline breakdown from the default handler', async () => {
    renderPage()
    expect(await screen.findByText('Overall verdict')).toBeInTheDocument()
    expect(screen.getByText('Stage breakdown')).toBeInTheDocument()
  })

  it('renders the error state when the pipeline cannot load', async () => {
    server.use(
      http.get('/api/v1/reviews/:taskId/pipeline', () =>
        HttpResponse.json(apiError('pipeline boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Pipeline result not available')).toBeInTheDocument()
  })
})
