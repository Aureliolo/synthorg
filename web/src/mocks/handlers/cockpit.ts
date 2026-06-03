import { http, HttpResponse } from 'msw'

import type {
  getCockpitSnapshot,
  getFlightRecorderFrames,
  getRedTeamReport,
  killTask,
  pauseTask,
  seekFlightRecorder,
} from '@/api/endpoints/cockpit'

import { buildTask } from './tasks'
import { paginatedFor, successFor } from './helpers'

export const cockpitHandlers = [
  http.get('/api/v1/cockpit/snapshot', () =>
    HttpResponse.json(
      successFor<typeof getCockpitSnapshot>({
        timestamp: '2026-05-22T12:00:00Z',
        agents: [
          {
            agent_id: 'agent-1',
            task_id: 'task-1',
            execution_id: 'exec-1',
            status: 'in_progress',
            turn_count: 3,
            cost: 0.45,
            last_active: '2026-05-22T11:58:00Z',
            is_stuck: false,
            is_runaway: false,
          },
        ],
        total_cost: 0.45,
        active_count: 1,
        stuck_agents: [],
        runaway_agents: [],
      }),
    ),
  ),
  http.get('/api/v1/cockpit/flight-recorder/:executionId/frames', ({ params }) => {
    const execId = String(params.executionId)
    const frames = [3, 2, 1].map((turn) => ({
      id: `${execId}-${String(turn)}`,
      execution_id: execId,
      task_id: 'task-1',
      agent_id: 'agent-1',
      turn_index: turn,
      timestamp: '2026-05-22T12:00:00Z',
      prompt_summary: null,
      response_summary: `turn ${String(turn)} response`,
      decision: 'completed',
      tool_calls: [],
      input_tokens: 40,
      output_tokens: 20,
      cost: 0.15,
      status: 'in_progress' as const,
      intervention_kind: null,
    }))
    return HttpResponse.json(
      paginatedFor<typeof getFlightRecorderFrames>({
        data: frames,
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: {
          limit: 50,
          next_cursor: null,
          has_more: false,
        },
      }),
    )
  }),
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
          truncated: false,
        }),
      ),
  ),
  http.get(
    '/api/v1/cockpit/flight-recorder/:executionId/red-team',
    ({ params }) => {
      const execId = String(params.executionId)
      return HttpResponse.json(
        successFor<typeof getRedTeamReport>({
          execution_id: execId,
          task_id: 'task-1',
          verdict: 'block',
          recorded_at: '2026-05-22T12:00:00Z',
          report: {
            execution_id: execId,
            task_id: 'task-1',
            summary: 'Adversarial review blocked completion.',
            findings: [
              {
                attack_surface: 'security',
                severity: 'high',
                description: 'Hardcoded credential in the deliverable.',
                evidence: ["api_key = 'sk-live'"],
                suggested_fix: 'Load the credential from a secret backend.',
                source: 'agent',
                citations: [],
              },
            ],
          },
        }),
      )
    },
  ),
  http.post('/api/v1/cockpit/interventions/pause', () =>
    HttpResponse.json(successFor<typeof pauseTask>(buildTask({ status: 'interrupted' }))),
  ),
  http.post('/api/v1/cockpit/interventions/kill', () =>
    HttpResponse.json(successFor<typeof killTask>(buildTask({ status: 'cancelled' }))),
  ),
]
