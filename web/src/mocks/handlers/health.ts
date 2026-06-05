import { http, HttpResponse } from 'msw'
import type { getLiveness, getReadiness } from '@/api/endpoints/health'
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
        version: '0.6.4',
        uptime_seconds: 0,
      }),
    ),
  ),
  // Readiness -- 200 on healthy persistence + message bus.
  http.get('/api/v1/readyz', () =>
    HttpResponse.json(
      successFor<typeof getReadiness>({
        status: 'ok',
        persistence: true,
        message_bus: true,
        providers: true,
        telemetry: 'disabled',
        version: '0.6.4',
        uptime_seconds: 0,
      }),
    ),
  ),
]
