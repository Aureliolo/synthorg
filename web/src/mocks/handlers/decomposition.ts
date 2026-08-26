import { http, HttpResponse } from 'msw'
import type { decomposeTaskManually } from '@/api/endpoints/decomposition'
import { apiError, successFor } from './helpers'
import { buildTask } from './tasks'

const _SUBTASK_ID = '11111111-1111-1111-1111-111111111111'

interface SubtaskSpec {
  readonly expected_artifacts?: readonly unknown[]
}

/** Whether every submitted subtask declares at least one deliverable. */
function everySubtaskDeclaresArtifacts(body: unknown): boolean {
  if (body === null || typeof body !== 'object' || !('subtasks' in body)) {
    return false
  }
  const subtasks = (body as { subtasks?: unknown }).subtasks
  if (!Array.isArray(subtasks) || subtasks.length === 0) return false
  return subtasks.every(
    (spec) => ((spec as SubtaskSpec).expected_artifacts ?? []).length > 0,
  )
}

export const decompositionHandlers = [
  http.post('/api/v1/tasks/:id/decompose', async ({ params, request }) => {
    // The backend rejects a work subtask that declares no deliverable, so a
    // handler that always succeeded would hide the one behaviour this form
    // exists to satisfy.
    const body: unknown = await request.json().catch(() => null)
    if (!everySubtaskDeclaresArtifacts(body)) {
      return HttpResponse.json(
        apiError("Field 'expected_artifacts' must be non-empty"),
        { status: 422 },
      )
    }
    return HttpResponse.json(
      successFor<typeof decomposeTaskManually>({
        plan: {
          parent_task_id: String(params['id']),
          subtasks: [
            {
              id: _SUBTASK_ID,
              title: 'Design',
              description: 'Design the feature.',
              dependencies: [],
              estimated_complexity: 'medium',
              stakes: 'normal',
              required_skills: [],
              required_tags: [],
              required_role: null,
              expected_artifacts: ['docs/design.md'],
              acceptance_criteria: ['the design is reviewed'],
              satisfies: [],
              unsplit_reason: null,
              kind: 'work',
              options: [],
            },
          ],
          open_questions: [],
          assumptions: [],
          // Null: the configured planner produced these items. A non-null
          // value means a fallback stood in, which the approval surface says.
          planning_strategy: null,
          task_structure: 'sequential',
          coordination_topology: 'auto',
        },
        created_tasks: [
          buildTask({ id: _SUBTASK_ID, parent_task_id: String(params['id']) }),
        ],
        dependency_edges: [],
        depth: 0,
        children: [],
      }),
    )
  }),
]
