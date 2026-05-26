import { http, HttpResponse } from 'msw'
import type {
  applyTemplatePack,
  listTemplatePacks,
} from '@/api/endpoints/template-packs'
import { apiError, successFor } from './helpers'

// ── Default test handlers (empty + typed apply). ──
export const templatePacksHandlers = [
  http.get('/api/v1/template-packs', () =>
    HttpResponse.json(successFor<typeof listTemplatePacks>([])),
  ),
  http.post('/api/v1/template-packs/apply', async ({ request }) => {
    const body = (await request.json()) as { pack_name?: string }
    if (!body.pack_name) {
      return HttpResponse.json(apiError("Field 'pack_name' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof applyTemplatePack>({
        pack_name: body.pack_name,
        agents_added: 0,
        departments_added: 0,
        budget_before: 0,
        budget_after: 0,
        rebalance_mode: 'none',
        scale_factor: null,
      }),
    )
  }),
]
