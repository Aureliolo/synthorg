/** Artifact, project, memory.fine_tune, and client WebSocket payload interfaces. */

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

export interface WsProjectStatusChangedPayload {
  project_id: string
  status: string
  previous_status?: string | null
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
