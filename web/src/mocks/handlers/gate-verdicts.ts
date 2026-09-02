import { http, HttpResponse } from 'msw'

import type {
  getCompletionOracleReports,
  getCompletionOracleSummary,
  getRedTeamReports,
  getRedTeamSummary,
} from '@/api/endpoints/gate-verdicts'

import { paginatedFor, successFor } from './helpers'

const EMPTY_PAGINATION = {
  limit: 20,
  next_cursor: null,
  has_more: false,
} as const

export const gateVerdictsHandlers = [
  http.get('/api/v1/completion-oracle/reports/summary', () =>
    HttpResponse.json(
      successFor<typeof getCompletionOracleSummary>({
        total: 4,
        by_verdict: { approve: 3, reject: 1, escalate: 0 },
      }),
    ),
  ),
  http.get('/api/v1/red-team/reports/summary', () =>
    HttpResponse.json(
      successFor<typeof getRedTeamSummary>({
        total: 2,
        by_verdict: { pass: 1, pass_with_findings: 0, block: 1 },
      }),
    ),
  ),
  http.get('/api/v1/completion-oracle/reports', () =>
    HttpResponse.json(
      paginatedFor<typeof getCompletionOracleReports>({
        data: [
          {
            report_id: 1,
            execution_id: 'exec-1',
            task_id: 'task-1',
            verdict: 'approve',
            recorded_at: '2026-08-14T09:00:00Z',
            reviewer_agent_id: 'agent-1',
            executor_agent_id: 'agent-2',
            reviewer_provider: 'example-provider',
            reviewer_model_id: 'example-capable-001',
            reviewer_capability: 'capable',
            report: {
              execution_id: 'exec-1',
              task_id: 'task-1',
              reviewer_agent_id: 'agent-1',
              executor_agent_id: 'agent-2',
              verdict: 'approve',
              findings: [],
              summary: 'The deliverable builds and its tests pass.',
              build_evidence_cited: true,
              test_evidence_cited: true,
              test_command: 'pytest',
            },
          },
        ],
        limit: 20,
        nextCursor: null,
        hasMore: false,
        pagination: EMPTY_PAGINATION,
      }),
    ),
  ),
  http.get('/api/v1/red-team/reports', () =>
    HttpResponse.json(
      paginatedFor<typeof getRedTeamReports>({
        data: [
          {
            report_id: 1,
            execution_id: 'exec-1',
            task_id: 'task-1',
            verdict: 'pass',
            recorded_at: '2026-08-14T09:00:00Z',
            red_team_agent_id: 'agent-1',
            executor_agent_id: 'agent-2',
            red_team_provider: 'example-provider',
            red_team_model_id: 'example-expert-001',
            red_team_capability: 'expert',
            report: {
              execution_id: 'exec-1',
              task_id: 'task-1',
              summary: 'No exploitable finding survived the attack.',
              findings: [],
            },
          },
        ],
        limit: 20,
        nextCursor: null,
        hasMore: false,
        pagination: EMPTY_PAGINATION,
      }),
    ),
  ),
]
