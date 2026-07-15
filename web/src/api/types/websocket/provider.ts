/** Artifact, project, memory.fine_tune, and client WebSocket payload interfaces. */

import type { PlanStatus, WorkflowExecutionStatus } from '../enum-values.gen'

export interface WsArtifactCreatedPayload {
  artifact_id: string
  task_id: string
  created_by: string
  type: string
}

export interface WsArtifactDeletedPayload {
  artifact_id: string
  task_id: string
}

export interface WsArtifactContentUploadedPayload {
  artifact_id: string
  size_bytes: number
  content_type: string
}

export interface WsProjectCreatedPayload {
  project_id: string
  name: string
  status: string
  lead?: string | null
}

export interface WsProjectDeletedPayload {
  project_id: string
  name: string
}

export interface WsProjectAutonomyModeChangedPayload {
  project_id: string
  new_mode?: string | null
  previous_mode?: string | null
}

export interface WsProjectStatusChangedPayload {
  project_id: string
  status: string
  previous_status?: string | null
}

export interface WsPlanUpdatedPayload {
  plan_id: string
  version: number
  status: PlanStatus
}

export interface WsPlanChangesRequestedPayload {
  plan_id: string
  status: PlanStatus
  note: string
}

export interface WsPlanCommentAddedPayload {
  plan_id: string
  item_id: string
  comment_id: string
  author: string
}

export interface WsWorkflowExecutionStatusChangedPayload {
  execution_id: string
  definition_id: string
  status: WorkflowExecutionStatus
  actor?: string | null
}

export interface WsMemoryFineTuneEventPayload {
  run_id: string
  stage: string
  progress?: number | null
}

export interface WsMemoryFineTuneStageChangedPayload extends WsMemoryFineTuneEventPayload {
  previous_stage?: string | null
}

export interface WsMemoryFineTuneFailedPayload extends WsMemoryFineTuneEventPayload {
  error?: string | null
}

export interface WsClientEventPayload {
  client_id: string
  name: string
  strictness_level: number
}

export interface WsClientDeletedPayload {
  client_id: string
  name?: string | null
}
