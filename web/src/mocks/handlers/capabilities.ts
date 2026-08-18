import { http, HttpResponse } from 'msw'

import type { getCapabilities } from '@/api/endpoints/capabilities'

import { successFor } from './helpers'

/**
 * Default capability matrix surfaces every optional subsystem as ``true`` so a
 * test that is not about capability gating does not have to opt in to every
 * flag to reach the surface it cares about. Per-test overrides via
 * ``server.use`` flip individual flags to exercise the gated UI paths.
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
        web_search: true,
        web_search_blocker: 'none',
        web_search_message: '',
        web_search_notify: false,
        web_search_reusable_connections: [],
        web_fetch: true,
      }),
    ),
  ),
]
