/** Request, review, simulation, interrupt, and dissent WebSocket payload interfaces. */

export interface WsRequestEventPayload {
  request_id: string
  client_id: string
  status: string
}

/**
 * Payload for ``request.task_created``. Mirrors
 * ``WsRequestTaskCreatedPayload`` in
 * ``src/synthorg/api/ws_payloads/_domain.py``: the backend stamps
 * ``task_id`` on every emission of this event so the dashboard can
 * navigate straight to the spawned task without a second
 * ``GET /requests/{id}`` round-trip.
 */
export interface WsRequestTaskCreatedPayload extends WsRequestEventPayload {
  task_id: string
}

export interface WsRequestStatusChangedPayload extends WsRequestEventPayload {
  previous_status?: string | null
}

export interface WsReviewStageCompletedPayload {
  task_id: string
  stage_name: string
}

export interface WsReviewStageDecidedPayload {
  task_id: string
  stage_name: string
  verdict: string
  decided_by: string
}

export interface WsReviewPipelineCompletedPayload {
  task_id: string
  final_verdict?: string | null
}

export interface WsSimulationEventPayload {
  simulation_id: string
  status: string
  progress: number
}

export interface WsSimulationFailedPayload extends WsSimulationEventPayload {
  error?: string | null
}

export interface WsInterruptCreatedPayload {
  interrupt_id: string
  task_id: string
  reason?: string | null
}

export interface WsInterruptResumedPayload {
  interrupt_id: string
  task_id: string
}

export interface WsDissentPublishedPayload {
  task_id: string
  agent_id: string
  message: string
}
