import type {
  FlightRecorderFrame,
  LiveActivitySnapshot,
  ReplaySeekView,
  SteeringOutcome,
  Task,
} from '@/api/types'

import {
  apiClient,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'

/** Fetch the live org-activity snapshot (who/what + stuck/runaway). */
export async function getCockpitSnapshot(): Promise<LiveActivitySnapshot> {
  const response =
    await apiClient.get<ApiResponse<LiveActivitySnapshot>>('/cockpit/snapshot')
  return unwrap(response)
}

/**
 * Fetch a page of flight-recorder frames (newest-first) for an execution.
 *
 * Uses opaque cursor pagination per the web dashboard's mandatory contract.
 * The returned envelope carries ``data`` (the frame page) and ``pagination``
 * (cursor + has_more) so callers can walk subsequent pages without
 * tracking offset arithmetic themselves.
 */
export async function getFlightRecorderFrames(
  executionId: string,
  params?: PaginationParams,
): Promise<PaginatedResult<FlightRecorderFrame>> {
  const response = await apiClient.get<PaginatedResponse<FlightRecorderFrame>>(
    `/cockpit/flight-recorder/${encodeURIComponent(executionId)}/frames`,
    { params },
  )
  return unwrapPaginated<FlightRecorderFrame>(response)
}

/** Reconstruct scrubber state at a target turn. */
export async function seekFlightRecorder(
  executionId: string,
  turnIndex: number,
): Promise<ReplaySeekView> {
  const response = await apiClient.get<ApiResponse<ReplaySeekView>>(
    `/cockpit/flight-recorder/${encodeURIComponent(executionId)}/seek/${turnIndex}`,
  )
  return unwrap(response)
}

/** Pause a running task (transition to INTERRUPTED). */
export async function pauseTask(taskId: string, reason: string): Promise<Task> {
  const response = await apiClient.post<ApiResponse<Task>>(
    '/cockpit/interventions/pause',
    { task_id: taskId, reason },
  )
  return unwrap(response)
}

/** Kill a running task (cancel it). */
export async function killTask(taskId: string, reason: string): Promise<Task> {
  const response = await apiClient.post<ApiResponse<Task>>(
    '/cockpit/interventions/kill',
    { task_id: taskId, reason },
  )
  return unwrap(response)
}

/** Queue a hint for a running agent (applied at the next safe turn boundary). */
export async function sendHint(
  executionId: string,
  agentId: string,
  text: string,
): Promise<SteeringOutcome> {
  const response = await apiClient.post<ApiResponse<SteeringOutcome>>(
    '/cockpit/interventions/hint',
    { execution_id: executionId, agent_id: agentId, text },
  )
  return unwrap(response)
}

/** Queue a redirect for a running agent. */
export async function redirectAgent(
  executionId: string,
  agentId: string,
  text: string,
): Promise<SteeringOutcome> {
  const response = await apiClient.post<ApiResponse<SteeringOutcome>>(
    '/cockpit/interventions/redirect',
    { execution_id: executionId, agent_id: agentId, text },
  )
  return unwrap(response)
}
