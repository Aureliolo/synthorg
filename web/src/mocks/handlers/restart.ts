import { http, HttpResponse } from 'msw'
import type { restartBackend } from '@/api/endpoints/restart'
import { successFor } from './helpers'

export const restartHandlers = [
  // Restart -- acknowledged before the process signals itself. Mirrors
  // ``web/src/api/endpoints/restart.ts`` 1:1 per the mandatory contract in
  // ``web/CLAUDE.md``.
  http.post('/api/v1/meta/restart', () =>
    HttpResponse.json(
      successFor<typeof restartBackend>({
        restarting: true,
        delay_seconds: 0.5,
      }),
      { status: 202 },
    ),
  ),
]
