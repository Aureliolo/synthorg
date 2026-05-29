import { fireEvent, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { renderRoutes } from '@/__tests__/test-utils'
import ReviewPipelinePage from '@/pages/ReviewPipelinePage'
import type { getReviewPipeline } from '@/api/endpoints/clients'

function seedPipelineWithStage() {
  server.use(
    http.get('/api/v1/reviews/:taskId/pipeline', ({ params }) =>
      HttpResponse.json(
        successFor<typeof getReviewPipeline>({
          task_id: String(params.taskId),
          final_verdict: 'pass',
          total_duration_ms: 12,
          reviewed_at: '2026-04-19T00:00:00Z',
          stage_results: [
            { stage_name: 'safety', verdict: 'pass', reason: 'ok', duration_ms: 5, metadata: {} },
          ],
        }),
      ),
    ),
  )
}

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

  it('records a stage override decision', async () => {
    seedPipelineWithStage()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Override pass' }))
    expect(await screen.findByText('Recorded PASS for safety')).toBeInTheDocument()
  })

  it('shows the action error when a stage decision fails', async () => {
    seedPipelineWithStage()
    server.use(
      http.post('/api/v1/reviews/:taskId/stages/:stageName/decide', () =>
        HttpResponse.json(apiError('decide boom'), { status: 500 }),
      ),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Override pass' }))
    expect(await screen.findByText('Stage action failed')).toBeInTheDocument()
  })
})
