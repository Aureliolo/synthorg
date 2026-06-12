import { http, HttpResponse } from 'msw'
import type {
  cancelFineTune,
  CheckpointRecord,
  deployCheckpoint,
  FineTuneStatus,
  getFineTuneStatus,
  listCheckpoints,
  listRuns,
  resumeFineTune,
  rollbackCheckpoint,
  runPreflight,
  startFineTune,
} from '@/api/endpoints/fine-tuning'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

const NOW = '2026-04-19T00:00:00Z'

const idleStatus: FineTuneStatus = {
  run_id: null,
  stage: 'idle',
  progress: null,
  error: null,
}

function buildCheckpoint(
  overrides: Partial<CheckpointRecord> = {},
): CheckpointRecord {
  return {
    id: 'ckpt-default',
    run_id: 'run-default',
    model_path: '/tmp/default',
    base_model: 'base-model',
    doc_count: 0,
    eval_metrics: null,
    size_bytes: 0,
    created_at: NOW,
    is_active: false,
    backup_config_json: null,
    ...overrides,
  }
}

export const fineTuningHandlers = [
  http.post('/api/v1/admin/memory/fine-tune', () =>
    HttpResponse.json(
      successFor<typeof startFineTune>({ ...idleStatus, run_id: 'run-new', stage: 'generating_data' }),
    ),
  ),
  http.post('/api/v1/admin/memory/fine-tune/resume/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof resumeFineTune>({
        ...idleStatus,
        run_id: String(params['id']),
        stage: 'training',
      }),
    ),
  ),
  http.get('/api/v1/admin/memory/fine-tune/status', () =>
    HttpResponse.json(successFor<typeof getFineTuneStatus>(idleStatus)),
  ),
  http.post('/api/v1/admin/memory/fine-tune/cancel', () =>
    HttpResponse.json(successFor<typeof cancelFineTune>(idleStatus)),
  ),
  http.post('/api/v1/admin/memory/fine-tune/preflight', () =>
    HttpResponse.json(
      successFor<typeof runPreflight>({
        checks: [],
        recommended_batch_size: null,
        can_proceed: true,
      }),
    ),
  ),
  http.get('/api/v1/admin/memory/fine-tune/checkpoints', () =>
    HttpResponse.json(paginatedFor<typeof listCheckpoints>(emptyPage<CheckpointRecord>())),
  ),
  http.post(
    '/api/v1/admin/memory/fine-tune/checkpoints/:id/deploy',
    ({ params }) =>
      HttpResponse.json(
        successFor<typeof deployCheckpoint>(
          buildCheckpoint({ id: String(params['id']), is_active: true }),
        ),
      ),
  ),
  http.post(
    '/api/v1/admin/memory/fine-tune/checkpoints/:id/rollback',
    ({ params }) =>
      HttpResponse.json(
        successFor<typeof rollbackCheckpoint>(
          buildCheckpoint({ id: String(params['id']) }),
        ),
      ),
  ),
  http.delete('/api/v1/admin/memory/fine-tune/checkpoints/:id', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.get('/api/v1/admin/memory/fine-tune/runs', () =>
    HttpResponse.json(paginatedFor<typeof listRuns>(emptyPage())),
  ),
]
