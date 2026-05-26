/** Barrel re-exports for ``@/api/types``.
 *
 * Consumer code should import every API type from this barrel:
 *
 *     import type { AgentConfig, TaskStatus } from '@/api/types'
 *
 * The barrel sources from four layers:
 *
 * - ``dtos.gen.ts``: every Pydantic DTO mirrored from the OpenAPI
 *   schema (regenerated via ``scripts/generate_dto_types_ts.py``).
 * - ``enum-values.gen.ts``: runtime ``*_VALUES`` tuples and the
 *   derived string-union types for every wire-facing StrEnum.
 * - ``enums.ts``: helper functions (e.g. ``isDepartmentName``) that
 *   compose with the generated tuples plus any frontend-only
 *   string unions that have no backend counterpart yet.
 * - ``http.ts``: the ``ApiResponse<T>`` / ``PaginatedResponse<T>``
 *   envelope generics. Litestar's monomorphised variants
 *   (``ApiResponse_AgentConfig_``) are aliased in ``dtos.gen.ts`` as
 *   ``<Name>Envelope`` / ``<Name>Page``; the named generic stays
 *   hand-maintained because the runtime axios call site uses
 *   ``apiClient.get<ApiResponse<AgentConfig>>(...)``.
 * - ``error-codes.gen.ts`` (via ``errors.ts``): the generated
 *   ``ErrorCode`` / ``ErrorCategory`` taxonomy plus the
 *   hand-written ``ErrorDetail`` envelope.
 * - ``websocket.ts``: the WebSocket wire contract (out of scope for
 *   the HTTP OpenAPI codegen; parity gated by
 *   ``scripts/check_ws_protocol_version_in_sync.py``).
 *
 * Name collisions between layers (``PaginationMeta``, ``ErrorCode``,
 * ``ErrorCategory``, ``ErrorDetail``) are resolved here in favour of
 * the hand-maintained source so the named ``ApiResponse<T>`` /
 * ``PaginatedResponse<T>`` generics in ``http.ts`` and the curated
 * ``ErrorCode`` taxonomy in ``error-codes.gen.ts`` win over the
 * auto-flattened ``components['schemas'][...]`` aliases in
 * ``dtos.gen.ts``.
 */

export type * from './dtos.gen'
export * from './enum-values.gen'
export * from './enums'
export type { ApiResponse, PaginatedResponse, PaginationMeta, PaginationParams } from './http'
export type { ErrorDetail } from './errors'
export * from './websocket'

// Hand-defined provider types that overlay or complement the generated
// DTOs in dtos.gen. Re-exported here so endpoint / mock / page modules
// import every provider type through the single ``@/api/types`` barrel
// instead of reaching into ``./providers`` directly.
export type {
  CredentialsRotateRequest,
  ProviderAuditEventType,
  ProviderConfig,
  ProviderPreset,
  PullProgressEvent,
  RateLimitsConfig,
} from './providers'
