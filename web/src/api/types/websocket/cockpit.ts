/**
 * Cockpit-channel WebSocket payloads (mid-flight steering).
 *
 * Mirrors the steering payload models in
 * ``src/synthorg/api/ws_payloads/_domain.py``. Every string field is
 * attacker-reachable (operator directive text forwarded verbatim), so
 * consumers MUST clamp through ``sanitizeWsString`` / ``sanitizeWsEnum``
 * at the dispatch boundary before display.
 */

/** ``steering.directive.issued`` -- a new directive went ACTIVE on a project. */
export interface WsSteeringDirectiveIssuedPayload {
  readonly project_id: string
  readonly directive_id: string
  /** ``InterventionKind`` value: ``hint`` or ``redirect``. */
  readonly kind: string
}

/** ``steering.supersession.proposed`` -- PROPOSE mode refined an obsolete set. */
export interface WsSteeringSupersessionProposedPayload {
  readonly project_id: string
  readonly directive_id: string
  readonly proposed_task_ids: readonly string[]
}

/** ``steering.tasks.superseded`` -- tasks were cancelled for a directive. */
export interface WsSteeringTasksSupersededPayload {
  readonly project_id: string
  readonly directive_id: string
  readonly task_ids: readonly string[]
}
