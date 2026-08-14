import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { CapabilitySourceDTO, CapabilitySourcesResponse } from '@/api/types/providers'
import { CapabilitySourcesSection } from '../CapabilitySourcesSection'

const BASE = '/api/v1/providers/capability-sources'

function source(overrides: Partial<CapabilitySourceDTO> = {}): CapabilitySourceDTO {
  return {
    label: 'source-a',
    display_name: 'Source A',
    enabled: true,
    feed_url: 'https://source-a.test/feed.csv',
    is_custom_url: false,
    axes: ['coding', 'reasoning', 'general'],
    licence_note: 'Creative Commons Attribution.',
    attribution: 'Benchmark data by Source A.',
    cadence_note: 'Refreshed daily.',
    last_attempted_at: '2026-08-13T06:00:00Z',
    last_succeeded_at: '2026-08-13T06:00:00Z',
    last_error: '',
    rows_read: 2249,
    rows_skipped: 12,
    scores_written: 700,
    evidence_age_days: 0.25,
    is_healthy: true,
    has_stale_evidence: false,
    ...overrides,
  }
}

function respondWith(sources: readonly CapabilitySourceDTO[]): void {
  const body: CapabilitySourcesResponse = {
    sources,
    any_healthy: sources.some((s) => s.enabled && s.is_healthy),
  }
  server.use(http.get(BASE, () => HttpResponse.json(apiSuccess(body))))
}

describe('CapabilitySourcesSection', () => {
  it('renders each source with what it read and how old that is', async () => {
    render(<CapabilitySourcesSection />)
    expect(await screen.findByText('Source A')).toBeInTheDocument()
    expect(screen.getByText(/700 measurements from 2249 rows/)).toBeInTheDocument()
    expect(screen.getByText(/measured today/)).toBeInTheDocument()
  })

  it('shows the licence and attribution the source requires', async () => {
    render(<CapabilitySourcesSection />)
    expect(
      await screen.findByText('Creative Commons Attribution.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Benchmark data by Source A.')).toBeInTheDocument()
  })

  it('marks a failing source whose earlier rows still grade as stale', async () => {
    // The state the panel exists for: the rungs still look graded, and the
    // only tell that nothing has refreshed them is this row.
    respondWith([
      source({
        is_healthy: false,
        has_stale_evidence: true,
        last_error: 'TimeoutError: upstream is not answering',
        evidence_age_days: 40,
      }),
    ])
    render(<CapabilitySourcesSection />)

    expect(await screen.findByText('Stale')).toBeInTheDocument()
    expect(
      screen.getByText('Not answering; earlier measurements still grading'),
    ).toBeInTheDocument()
    expect(screen.getByText(/measured 40 days ago/)).toBeInTheDocument()
  })

  it('separates a source with no evidence at all from a stale one', async () => {
    respondWith([
      source({
        is_healthy: false,
        has_stale_evidence: false,
        last_succeeded_at: null,
        last_error: 'TimeoutError: upstream is not answering',
        evidence_age_days: null,
        rows_read: 0,
        scores_written: 0,
      }),
    ])
    render(<CapabilitySourcesSection />)

    expect(await screen.findByText('No evidence')).toBeInTheDocument()
    expect(screen.getByText('No successful read yet.')).toBeInTheDocument()
  })

  it('says grading continues when one source is down and another is up', async () => {
    respondWith([
      source(),
      source({
        label: 'source-b',
        display_name: 'Source B',
        is_healthy: false,
        has_stale_evidence: true,
        last_error: 'TimeoutError',
      }),
    ])
    render(<CapabilitySourcesSection />)

    expect(await screen.findByText('One source is not answering')).toBeInTheDocument()
    expect(screen.getByText(/grading is running on the sources that are left/)).toBeInTheDocument()
  })

  it('says grading fell back to the heuristic when every source is down', async () => {
    respondWith([
      source({ is_healthy: false, has_stale_evidence: false, last_error: 'TimeoutError' }),
    ])
    render(<CapabilitySourcesSection />)

    expect(await screen.findByText('No enabled source is answering')).toBeInTheDocument()
    expect(screen.getByText(/falls? back to the size-and-price heuristic/)).toBeInTheDocument()
  })

  it('does not warn when every enabled source is answering', async () => {
    respondWith([source()])
    render(<CapabilitySourcesSection />)
    await screen.findByText('Source A')
    expect(screen.queryByText(/is not answering/)).not.toBeInTheDocument()
  })

  it('reports a disabled source as off rather than broken', async () => {
    respondWith([source({ enabled: false, is_healthy: false, last_error: '' })])
    render(<CapabilitySourcesSection />)

    expect(await screen.findByText('Off')).toBeInTheDocument()
    expect(screen.queryByText(/is not answering/)).not.toBeInTheDocument()
  })

  it('flags a source an operator re-pointed', async () => {
    respondWith([source({ is_custom_url: true })])
    render(<CapabilitySourcesSection />)
    expect(await screen.findByText('Custom URL')).toBeInTheDocument()
  })

  it('refreshes one source on demand', async () => {
    let refreshed = ''
    respondWith([source()])
    server.use(
      http.post(`${BASE}/:label/refresh`, ({ params }) => {
        refreshed = String(params['label'])
        return HttpResponse.json(apiSuccess({ sources: [source()], any_healthy: true }))
      }),
    )
    render(<CapabilitySourcesSection />)
    await screen.findByText('Source A')

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(refreshed).toBe('source-a')
    })
  })

  it('disables a source through the API', async () => {
    let body: unknown = null
    respondWith([source()])
    server.use(
      http.put(`${BASE}/:label`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(
          apiSuccess({ sources: [source({ enabled: false })], any_healthy: false }),
        )
      }),
    )
    render(<CapabilitySourcesSection />)
    await screen.findByText('Source A')

    fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }))

    await waitFor(() => {
      // No feed_url at all. The write is a full replace and an empty
      // string means "reset to the shipped default", so sending one here
      // would discard an operator's custom URL every time the switch was
      // touched. Omitting it leaves the configured URL alone.
      expect(body).toEqual({ enabled: false })
    })
  })

  it('surfaces a load failure with a retry', async () => {
    server.use(
      http.get(BASE, () => HttpResponse.json(apiError('Boom'), { status: 500 })),
    )
    render(<CapabilitySourcesSection />)

    expect(
      await screen.findByText('Could not load the capability sources'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('renders an empty state when nothing is declared', async () => {
    respondWith([])
    render(<CapabilitySourcesSection />)

    expect(
      await screen.findByText('No capability sources are declared'),
    ).toBeInTheDocument()
  })
})
