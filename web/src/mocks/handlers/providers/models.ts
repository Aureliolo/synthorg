import { http, HttpResponse } from 'msw'
import type {
  addProviderModel,
  reenableToolCalling,
  syncProviderModels,
  updateModelConfig,
} from '@/api/endpoints/providers'
import type {
  AddModelRequest,
  UpdateModelConfigRequest,
} from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor, voidSuccess } from '../helpers'
import { buildProvider } from './crud'

/**
 * Default SSE stream emits one completion event, suitable for tests
 * that just verify pullModel resolves. Streaming-specific tests
 * should override per-case via ``server.use(...)``.
 */
function buildPullStream(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          'event: progress\ndata: {"status":"complete","progress_percent":100,"total_bytes":null,"completed_bytes":null,"error":null,"done":true}\n\n',
        ),
      )
      controller.close()
    },
  })
}

export const modelsHandlers = [
  http.post('/api/v1/providers/:name/models/pull', () =>
    new HttpResponse(buildPullStream(), {
      headers: { 'Content-Type': 'text/event-stream' },
    }),
  ),
  http.delete('/api/v1/providers/:name/models/:modelId', () =>
    HttpResponse.json(voidSuccess()),
  ),
  // ``encodeModelIdPath`` keeps the ``/`` separators inside a model id, so the
  // modelId segment can span multiple path segments; a single ``:modelId``
  // named param would miss those, hence the wildcard.
  http.post(
    '/api/v1/providers/:name/models/*/reenable-tool-calling',
    ({ params }) =>
      HttpResponse.json(
        successFor<typeof reenableToolCalling>(
          buildProvider({ name: decodeURIComponent(String(params['name'])) }),
        ),
        // Litestar @post defaults to 201; mirror it so status assertions match.
        { status: 201 },
      ),
  ),
  http.put('/api/v1/providers/:name/models/:modelId/config', async ({ params, request }) => {
    // Echo the mutated model so tests that reconcile by id (or that
    // expect ``local_params`` to round-trip) see their own write
    // instead of a fixed ``model-default`` placeholder. The route
    // ``:modelId`` is the canonical id; the request body carries the
    // launch-parameter overrides.
    const body = (await request.json()) as UpdateModelConfigRequest
    return HttpResponse.json(
      successFor<typeof updateModelConfig>({
        id: decodeURIComponent(String(params['modelId'])),
        alias: null,
        cost_per_1k_input: 0,
        cost_per_1k_output: 0,
        cost_per_image: null,
        currency: DEFAULT_CURRENCY,
        max_context: 0,
        estimated_latency_ms: null,
        local_params: body.local_params,
        supports_tools: false,
        tool_calls_verified: null,
        supports_vision: false,
        supports_streaming: false,
        supports_embeddings: false,
        supports_reasoning: false,
        supports_image_generation: false,
        family: null,
        metadata_source: 'unknown',
        stale: null,
      }),
    )
  }),
  http.post('/api/v1/providers/:name/models', async ({ params, request }) => {
    // Echo the added model in ``models`` and stamp ``name`` from the
    // route param so callers reconciling the returned provider see
    // their addition reflected. Promotes ``ProviderModelConfig`` to
    // the response ``ProviderModelResponse`` shape with the extra
    // capability flags defaulted to ``false``.
    const body = (await request.json()) as AddModelRequest
    const newModel = {
      ...body.model,
      currency: DEFAULT_CURRENCY,
      supports_tools: false,
      tool_calls_verified: null,
      supports_vision: false,
      supports_streaming: false,
      supports_embeddings: false,
      supports_reasoning: false,
      supports_image_generation: false,
      family: null,
      metadata_source: 'unknown',
      stale: null,
    }
    return HttpResponse.json(
      successFor<typeof addProviderModel>(
        buildProvider({
          name: decodeURIComponent(String(params['name'])),
          models: [newModel],
        }),
      ),
    )
  }),
  http.post('/api/v1/providers/:name/models/sync', () =>
    HttpResponse.json(
      successFor<typeof syncProviderModels>({
        added: [],
        removed: [],
        updated: [],
        models: [],
      }),
    ),
  ),
]
