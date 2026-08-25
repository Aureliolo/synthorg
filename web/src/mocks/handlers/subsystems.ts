import { http, HttpResponse } from 'msw'
import type { getSubsystems } from '@/api/endpoints/subsystems'
import { successFor } from './helpers'

export const subsystemHandlers = [
  // A fully wired org: every declared subsystem active, nothing waiting. Tests
  // that care about a blocked subsystem override this with ``server.use(...)``.
  http.get('/api/v1/subsystems', () =>
    HttpResponse.json(
      successFor<typeof getSubsystems>({
        subsystems: [
          { name: 'memory_backend', phase: 'active', waiting_on: [], detail: null },
          { name: 'docs_engine', phase: 'active', waiting_on: [], detail: null },
        ],
        active: 2,
        degraded: 0,
        waiting: 0,
        unreachable: 0,
        rebuilding: 0,
        blocked: 0,
        failed: 0,
        disabled: 0,
      }),
    ),
  ),
]
