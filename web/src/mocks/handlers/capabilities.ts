import { http, HttpResponse } from 'msw'

import type { getCapabilities } from '@/api/endpoints/capabilities'

import { successFor } from './helpers'

/**
 * Default capability matrix surfaces every optional subsystem as
 * ``true`` so existing test cases (which were written against the
 * older always-registered routes, before capability gating) continue
 * to fire. Per-test overrides via ``server.use`` can flip individual
 * flags to exercise the gated UI paths.
 */
export const capabilitiesHandlers = [
  http.get('/api/v1/capabilities/', () =>
    HttpResponse.json(
      successFor<typeof getCapabilities>({
        simulations: true,
        requests: true,
        ontology: true,
        tunnel: true,
        webhooks: true,
        a2a: true,
        telemetry: false,
        integrations: true,
      }),
    ),
  ),
]
