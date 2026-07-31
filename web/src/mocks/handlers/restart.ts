import { http, HttpResponse } from 'msw'
import type { getRestartStatus, restartBackend } from '@/api/endpoints/restart'
import { successFor } from './helpers'

export const restartHandlers = [
  // Restart status -- what a restart would apply, derived backend-side.
  // Mirrors ``web/src/api/endpoints/restart.ts`` 1:1 per the mandatory
  // contract in ``web/CLAUDE.md``.
  http.get('/api/v1/meta/restart', () =>
    HttpResponse.json(
      successFor<typeof getRestartStatus>({
        pending: [],
        supervised: true,
      }),
    ),
  ),
  // Restart -- acknowledged before the process signals itself.
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
