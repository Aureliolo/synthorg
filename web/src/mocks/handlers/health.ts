import { http, HttpResponse } from 'msw'
import type {
  getHealthDetail,
  getLiveness,
  getReadiness,
} from '@/api/endpoints/health'
import { successFor } from './helpers'

export const healthHandlers = [
  // Liveness -- always 200 while the process is alive. MSW handlers in
  // ``web/src/mocks/handlers/`` mirror ``web/src/api/endpoints/*.ts``
  // 1:1 per the mandatory contract in ``web/CLAUDE.md``, so every
  // exported endpoint function gets a default happy-path handler.
  http.get('/api/v1/healthz', () =>
    HttpResponse.json(
      successFor<typeof getLiveness>({
        status: 'ok',
        uptime_seconds: 0,
      }),
    ),
  ),
  // Readiness -- topology- and version-free public probe (status + uptime).
  http.get('/api/v1/readyz', () =>
    HttpResponse.json(
      successFor<typeof getReadiness>({
        status: 'ok',
        uptime_seconds: 0,
      }),
    ),
  ),
  // Health detail -- authenticated per-component breakdown.
  http.get('/api/v1/health', () =>
    HttpResponse.json(
      successFor<typeof getHealthDetail>({
        status: 'ok',
        persistence: true,
        message_bus: true,
        providers: 'ok',
        telemetry: 'disabled',
        memory: { state: 'durable', backend: 'sqlvector', detail: null },
        backup: { state: 'wired', detail: null },
        cost_recording: { state: 'ok', dropped_records: 0, detail: null },
        version: '0.6.4',
        uptime_seconds: 0,
      }),
    ),
  ),
]
