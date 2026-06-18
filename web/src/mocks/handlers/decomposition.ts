import { http, HttpResponse } from 'msw'
import type { decomposeTaskManually } from '@/api/endpoints/decomposition'
import { successFor } from './helpers'
import { buildTask } from './tasks'

const _SUBTASK_ID = '11111111-1111-1111-1111-111111111111'

export const decompositionHandlers = [
  http.post('/api/v1/tasks/:id/decompose', ({ params }) =>
    HttpResponse.json(
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
            },
          ],
          task_structure: 'sequential',
          coordination_topology: 'auto',
        },
        created_tasks: [
          buildTask({ id: _SUBTASK_ID, parent_task_id: String(params['id']) }),
        ],
        dependency_edges: [],
      }),
    ),
  ),
]
