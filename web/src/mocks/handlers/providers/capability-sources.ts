import { http, HttpResponse } from 'msw'
import type {
  ingestCapabilitySourceRows,
  listCapabilitySources,
  refreshCapabilitySource,
  refreshDueCapabilitySources,
  setCapabilitySource,
} from '@/api/endpoints/providers'
import type { CapabilitySourcesResponse } from '@/api/types/providers'
import { successFor } from '../helpers'

const BASE = '/api/v1/providers/capability-sources'

function buildSources(): CapabilitySourcesResponse {
  return {
    // One healthy source and one that is failing while its earlier rows
    // still grade: the pair a panel has to tell apart.
    sources: [
      {
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
        rows_skipped: 0,
        scores_written: 700,
        evidence_age_days: 0.25,
        is_healthy: true,
        has_stale_evidence: false,
      },
      {
        label: 'source-b',
        display_name: 'Source B',
        enabled: true,
        feed_url: 'https://source-b.test/feed.parquet',
        is_custom_url: false,
        axes: ['coding', 'reasoning', 'general'],
        licence_note: 'Creative Commons Attribution 4.0.',
        attribution: 'Leaderboard data by Source B.',
        cadence_note: 'Republished daily.',
        last_attempted_at: '2026-08-13T06:00:00Z',
        last_succeeded_at: '2026-07-04T06:00:00Z',
        last_error: 'TimeoutError: upstream is not answering',
        rows_read: 10262,
        rows_skipped: 6489,
        scores_written: 1162,
        evidence_age_days: 40.0,
        is_healthy: false,
        has_stale_evidence: true,
      },
    ],
    any_healthy: true,
  }
}

export const capabilitySourcesHandlers = [
  http.get(BASE, () =>
    HttpResponse.json(successFor<typeof listCapabilitySources>(buildSources())),
  ),
  http.post(`${BASE}/refresh`, () =>
    HttpResponse.json(successFor<typeof refreshDueCapabilitySources>(buildSources())),
  ),
  http.post(`${BASE}/:label/refresh`, () =>
    HttpResponse.json(successFor<typeof refreshCapabilitySource>(buildSources())),
  ),
  http.post(`${BASE}/:label/rows`, () =>
    HttpResponse.json(successFor<typeof ingestCapabilitySourceRows>(buildSources())),
  ),
  http.put(`${BASE}/:label`, () =>
    HttpResponse.json(successFor<typeof setCapabilitySource>(buildSources())),
  ),
]
