import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect } from 'vitest'
import { apiError, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import LearningCurvePage from '@/pages/LearningCurvePage'
import type { getLearningCurve } from '@/api/endpoints/learning'
import type { LearningCurvePoint } from '@/api/types'

function renderPage() {
  return render(
    <MemoryRouter>
      <LearningCurvePage />
    </MemoryRouter>,
  )
}

function point(overrides: Partial<LearningCurvePoint> & { total: number }): LearningCurvePoint {
  const { total } = overrides
  return {
    run_label: `run-${total}`,
    generated_at: '2026-01-01T00:00:00Z',
    max_total: 100,
    is_passing: total >= 60,
    delta: 0,
    is_regression: false,
    score_fraction: total / 100,
    ...overrides,
  }
}

describe('LearningCurvePage', () => {
  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Learning curve')).toBeInTheDocument()
  })

  it('renders the empty state from the default handler', async () => {
    renderPage()
    expect(await screen.findByText('No benchmark runs recorded')).toBeInTheDocument()
  })

  it('renders summary cards and the chart for a recorded curve', async () => {
    server.use(
      http.get('/api/v1/learning/curve', () =>
        HttpResponse.json(
          successFor<typeof getLearningCurve>({
            points: [
              point({ total: 40 }),
              point({ total: 70, delta: 30, generated_at: '2026-01-02T00:00:00Z' }),
            ],
            has_regression: false,
            latest_total: 70,
          }),
        ),
      ),
    )
    renderPage()
    expect(await screen.findByTestId('benchmark-score-chart')).toBeInTheDocument()
    expect(screen.getByText('Latest score')).toBeInTheDocument()
    expect(screen.getByText('Runs recorded')).toBeInTheDocument()
  })

  it('surfaces a warning banner when the curve has a regression', async () => {
    server.use(
      http.get('/api/v1/learning/curve', () =>
        HttpResponse.json(
          successFor<typeof getLearningCurve>({
            points: [
              point({ total: 80 }),
              point({
                total: 20,
                delta: -60,
                is_regression: true,
                generated_at: '2026-01-02T00:00:00Z',
              }),
            ],
            has_regression: true,
            latest_total: 20,
          }),
        ),
      ),
    )
    renderPage()
    expect(await screen.findByText('Benchmark regression detected')).toBeInTheDocument()
  })

  it('renders the error banner when the fetch fails', async () => {
    server.use(
      http.get('/api/v1/learning/curve', () =>
        HttpResponse.json(apiError('curve boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Could not load learning curve')).toBeInTheDocument()
  })
})
