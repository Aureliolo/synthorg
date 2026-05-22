import { http, HttpResponse } from 'msw'

import type {
  getCockpitSnapshot,
  getFlightRecorderFrames,
  killTask,
  pauseTask,
  redirectAgent,
  seekFlightRecorder,
  sendHint,
} from '@/api/endpoints/cockpit'

import { buildTask } from './tasks'
import { successFor } from './helpers'

export const cockpitHandlers = [
  http.get('/api/v1/cockpit/snapshot', () =>
    HttpResponse.json(
      successFor<typeof getCockpitSnapshot>({
        timestamp: '2026-05-22T12:00:00Z',
        agents: [],
        total_cost: 0,
        active_count: 0,
        stuck_agents: [],
        runaway_agents: [],
      }),
    ),
  ),
  http.get('/api/v1/cockpit/flight-recorder/:executionId/frames', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getFlightRecorderFrames>({
        execution_id: String(params.executionId),
        frames: [],
      }),
    ),
  ),
  http.get(
    '/api/v1/cockpit/flight-recorder/:executionId/seek/:turnIndex',
    ({ params }) =>
      HttpResponse.json(
        successFor<typeof seekFlightRecorder>({
          execution_id: String(params.executionId),
          turn_index: Number(params.turnIndex),
          frames: [],
          current_frame: null,
          cumulative_cost: 0,
        }),
      ),
  ),
  http.post('/api/v1/cockpit/interventions/pause', () =>
    HttpResponse.json(successFor<typeof pauseTask>(buildTask({ status: 'interrupted' }))),
  ),
  http.post('/api/v1/cockpit/interventions/kill', () =>
    HttpResponse.json(successFor<typeof killTask>(buildTask({ status: 'cancelled' }))),
  ),
  http.post('/api/v1/cockpit/interventions/hint', () =>
    HttpResponse.json(
      successFor<typeof sendHint>({
        kind: 'hint',
        applied: true,
        artifact_id: 'interrupt-1',
        detail: 'queued, awaiting the agent next safe turn boundary',
      }),
    ),
  ),
  http.post('/api/v1/cockpit/interventions/redirect', () =>
    HttpResponse.json(
      successFor<typeof redirectAgent>({
        kind: 'redirect',
        applied: true,
        artifact_id: 'interrupt-2',
        detail: 'queued, awaiting the agent next safe turn boundary',
      }),
    ),
  ),
]
