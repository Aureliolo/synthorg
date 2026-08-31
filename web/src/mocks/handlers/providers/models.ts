import { http, HttpResponse } from 'msw'
import type {
  addProviderModel,
  reenableToolCalling,
  syncProviderModels,
  updateModelCapabilityOverrides,
  updateModelConfig,
} from '@/api/endpoints/providers'
import type {
  AddModelRequest,
  CapabilityOverridesUpdateRequest,
  ModelCapabilityOverrides,
  UpdateModelConfigRequest,
} from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor, voidSuccess } from '../helpers'
import { buildProvider } from './crud'

/**
 * ``ModelCapabilityOverrides`` (the reply shape) requires every field;
 * ``CapabilityOverridesUpdateRequest`` (the request shape) may omit one to
 * mean "leave unchanged", so an omitted field defaults to ``null`` (no
 * override) rather than being dropped.
 */
function toModelCapabilityOverrides(
  body: CapabilityOverridesUpdateRequest,
): ModelCapabilityOverrides {
  return {
    supports_tools: body.supports_tools ?? null,
    supports_vision: body.supports_vision ?? null,
    supports_streaming: body.supports_streaming ?? null,
    supports_embeddings: body.supports_embeddings ?? null,
    supports_image_generation: body.supports_image_generation ?? null,
    supports_reasoning: body.supports_reasoning ?? null,
    supports_prompt_caching: body.supports_prompt_caching ?? null,
  }
}

/** Fold overrides into the flattened booleans every other model consumer reads. */
function flattenOverrides(overrides: ModelCapabilityOverrides) {
  return {
    supports_tools: overrides.supports_tools ?? false,
    supports_vision: overrides.supports_vision ?? false,
    supports_streaming: overrides.supports_streaming ?? true,
    supports_embeddings: overrides.supports_embeddings ?? false,
    supports_reasoning: overrides.supports_reasoning ?? false,
    supports_image_generation: overrides.supports_image_generation ?? false,
    supports_prompt_caching: overrides.supports_prompt_caching ?? false,
  }
}

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
        capability_overrides: null,
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
        supports_prompt_caching: false,
        family: null,
        metadata_source: 'unknown',
        stale: null,
      }),
    )
  }),
  http.patch(
    '/api/v1/providers/:name/models/*/capabilities',
    async ({ params, request }) => {
      // Echo the requested overrides both as ``capability_overrides`` (what
      // the drawer reads back to pre-populate its selects) and folded into
      // the flattened booleans (what every other consumer of the model
      // reads), so a round-trip through this handler reflects a save
      // immediately rather than needing a full page reload.
      const body = (await request.json()) as CapabilityOverridesUpdateRequest
      const overrides = toModelCapabilityOverrides(body)
      return HttpResponse.json(
        successFor<typeof updateModelCapabilityOverrides>({
          id: decodeURIComponent(String(params['0'])),
          alias: null,
          capability_overrides: overrides,
          cost_per_1k_input: 0,
          cost_per_1k_output: 0,
          cost_per_image: null,
          currency: DEFAULT_CURRENCY,
          max_context: 0,
          estimated_latency_ms: null,
          local_params: null,
          tool_calls_verified: null,
          ...flattenOverrides(overrides),
          family: null,
          metadata_source: 'unknown',
          stale: null,
        }),
      )
    },
  ),
  http.post('/api/v1/providers/:name/models', async ({ params, request }) => {
    // ``POST .../models`` replies with the plain ``ProviderResponse``
    // (``ProviderConfig`` on the wire), whose ``models`` are
    // ``ProviderModelConfig`` -- the request shape itself, capability
    // metadata still nested under ``model.metadata`` -- never the
    // flattened-boolean ``ProviderModelResponse`` the list/detail GET
    // endpoints return. Echo the submitted model unchanged so callers
    // reconciling the returned provider see their addition reflected.
    const body = (await request.json()) as AddModelRequest
    return HttpResponse.json(
      successFor<typeof addProviderModel>(
        buildProvider({
          name: decodeURIComponent(String(params['name'])),
          models: [body.model],
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
