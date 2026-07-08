import { http, HttpResponse } from 'msw'
import type { getBoard } from '@/api/endpoints/board'
import { successFor } from './helpers'

export const boardHandlers = [
  http.get('/api/v1/board', () =>
    HttpResponse.json(
      successFor<typeof getBoard>({
        workflow_type: 'agile_kanban',
        enforce_wip: false,
        columns: [
          { column: 'backlog', tasks: [], count: 0, limit: null, over_limit: false },
          { column: 'ready', tasks: [], count: 0, limit: null, over_limit: false },
          {
            column: 'in_progress',
            tasks: [],
            count: 2,
            limit: 5,
            over_limit: false,
          },
          { column: 'review', tasks: [], count: 1, limit: 3, over_limit: false },
          { column: 'done', tasks: [], count: 0, limit: null, over_limit: false },
        ],
      }),
    ),
  ),
]
