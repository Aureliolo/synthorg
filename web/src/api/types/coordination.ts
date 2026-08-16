/** Multi-agent coordination request/response types. */

// The ROW is what the endpoint returns: the run plus its task title and lead
// agent's name. The dashboard has no other coordination-metrics shape.
export type { CoordinationMetricsRow as CoordinationMetricsRecord } from './dtos.gen'
export type {
  CoordinateTaskRequest,
  CoordinationResultResponse,
} from './dtos.gen'
