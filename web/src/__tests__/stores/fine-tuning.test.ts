import { http, HttpResponse } from 'msw'
import {
  selectFineTuningBannerError,
  useFineTuningStore,
} from '@/stores/fine-tuning'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import type { PaginatedResult } from '@/api/client'
import { server } from '@/test-setup'
import type {
  CheckpointRecord,
  FineTuneRun,
  FineTuneStatus,
  listCheckpoints,
  listRuns,
  PreflightResult,
} from '@/api/endpoints/fine-tuning'

const BASE_STATUS: FineTuneStatus = {
  run_id: 'run-1',
  stage: 'idle',
  progress: null,
  error: null,
}

const BASE_CHECKPOINT: CheckpointRecord = {
  id: 'ckpt-1',
  run_id: 'run-1',
  model_path: '/models/ckpt-1',
  base_model: 'small-base',
  doc_count: 100,
  eval_metrics: null,
  size_bytes: 0,
  created_at: '2026-04-28T08:00:00+00:00',
  is_active: false,
  backup_config_json: null,
}

const BASE_RUN: FineTuneRun = {
  id: 'run-1',
  stage: 'complete',
  progress: 1,
  error: null,
  config: {
    source_dir: 'data',
    base_model: 'small-base',
    output_dir: 'models',
    epochs: 1,
    learning_rate: 0.001,
    temperature: 1,
    top_k: 5,
    batch_size: 8,
    validation_split: 0.1,
  },
  started_at: '2026-04-28T08:00:00+00:00',
  updated_at: '2026-04-28T08:30:00+00:00',
  completed_at: '2026-04-28T08:30:00+00:00',
  duration_seconds: 1800,
  stages_completed: ['training'],
}

const BASE_PREFLIGHT: PreflightResult = {
  checks: [
    { name: 'data', status: 'pass', message: 'OK', detail: null },
  ],
  recommended_batch_size: 8,
  can_proceed: true,
}

function pageOf<T>(items: readonly T[]): PaginatedResult<T> {
  return {
    data: [...items],
    limit: 50,
    nextCursor: null,
    hasMore: false,
    pagination: { limit: 50, next_cursor: null, has_more: false },
  }
}

describe('useFineTuningStore', () => {
  beforeEach(() => {
    useFineTuningStore.setState({
      status: null,
      checkpoints: [],
      checkpointsPagination: { nextCursor: null, hasMore: false },
      runs: [],
      runsPagination: { nextCursor: null, hasMore: false },
      preflight: null,
      loading: false,
      errors: { status: null, checkpoints: null, runs: null },
    })
    useToastStore.getState().dismissAll()
  })

  describe('fetch actions (set error, no toast)', () => {
    it('fetchStatus populates status and clears error', async () => {
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/status', () =>
          HttpResponse.json(apiSuccess(BASE_STATUS)),
        ),
      )

      await useFineTuningStore.getState().fetchStatus()

      expect(useFineTuningStore.getState().status).toEqual(BASE_STATUS)
      expect(useFineTuningStore.getState().errors.status).toBeNull()
    })

    it('fetchStatus sets error and emits no toast on failure', async () => {
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/status', () =>
          HttpResponse.json(apiError('Backend offline'), { status: 503 }),
        ),
      )

      await useFineTuningStore.getState().fetchStatus()

      // 503 with no Retry-After header signals a sustained outage,
      // so the toast must escalate ("unavailable") rather than promise
      // a retry duration the backend has not provided.
      expect(useFineTuningStore.getState().errors.status).toContain('unavailable')
      // Fetch failures stay on the page-level ErrorBanner; no toast.
      expect(useToastStore.getState().toasts).toHaveLength(0)
    })

    it('fetchCheckpoints populates checkpoints on success', async () => {
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/checkpoints', () =>
          HttpResponse.json(paginatedFor<typeof listCheckpoints>(pageOf([BASE_CHECKPOINT]))),
        ),
      )

      await useFineTuningStore.getState().fetchCheckpoints()

      expect(useFineTuningStore.getState().checkpoints).toEqual([BASE_CHECKPOINT])
    })

    it('fetchCheckpoints sets error and emits no toast on failure', async () => {
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/checkpoints', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )

      await useFineTuningStore.getState().fetchCheckpoints()

      expect(useFineTuningStore.getState().errors.checkpoints).not.toBeNull()
      expect(useToastStore.getState().toasts).toHaveLength(0)
    })

    it('concurrent fetches do not erase each others errors', async () => {
      // The page bootstraps fetchStatus / fetchCheckpoints / fetchRuns in
      // parallel. With the previous shared `error` field, a successful
      // later fetch would clear an earlier failure. The per-resource map
      // keeps each resource's outcome independent, so the failed status
      // banner survives a successful checkpoints / runs response.
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/status', () =>
          HttpResponse.json(apiError('Backend offline'), { status: 503 }),
        ),
        http.get('/api/v1/admin/memory/fine-tune/checkpoints', () =>
          HttpResponse.json(paginatedFor<typeof listCheckpoints>(pageOf([BASE_CHECKPOINT]))),
        ),
        http.get('/api/v1/admin/memory/fine-tune/runs', () =>
          HttpResponse.json(paginatedFor<typeof listRuns>(pageOf([BASE_RUN]))),
        ),
      )

      const store = useFineTuningStore.getState()
      await Promise.all([
        store.fetchStatus(),
        store.fetchCheckpoints(),
        store.fetchRuns(),
      ])

      const after = useFineTuningStore.getState()
      expect(after.checkpoints).toEqual([BASE_CHECKPOINT])
      expect(after.runs).toEqual([BASE_RUN])
      expect(after.errors.status).not.toBeNull()
      expect(after.errors.checkpoints).toBeNull()
      expect(after.errors.runs).toBeNull()
      expect(selectFineTuningBannerError(after.errors)).toBe(after.errors.status)
      expect(useToastStore.getState().toasts).toHaveLength(0)
    })

    it('fetchRuns populates runs on success', async () => {
      server.use(
        http.get('/api/v1/admin/memory/fine-tune/runs', () =>
          HttpResponse.json(paginatedFor<typeof listRuns>(pageOf([BASE_RUN]))),
        ),
      )

      await useFineTuningStore.getState().fetchRuns()

      expect(useFineTuningStore.getState().runs).toEqual([BASE_RUN])
    })
  })

  describe('mutation actions (toast on failure, no error set)', () => {
    it('startRun emits error toast on failure and leaves error field unset', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune', () =>
          HttpResponse.json(apiError('Insufficient credits'), { status: 402 }),
        ),
      )

      await useFineTuningStore.getState().startRun({ source_dir: 'data' })

      // errors stay null because mutations toast; the store error map is
      // reserved for fetch failures so the page banner shows fetch
      // errors only.
      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      expect(useFineTuningStore.getState().loading).toBe(false)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.description).toContain('Insufficient credits')
    })

    it('cancelRun emits error toast on failure', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune/cancel', () =>
          HttpResponse.json(apiError('Already idle'), { status: 409 }),
        ),
      )

      await useFineTuningStore.getState().cancelRun()

      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Resource conflict')
    })

    it('runPreflightCheck emits error toast and clears loading on failure', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune/preflight', () =>
          HttpResponse.json(apiError('No data found'), { status: 404 }),
        ),
      )

      await useFineTuningStore.getState().runPreflightCheck({ source_dir: 'data' })

      expect(useFineTuningStore.getState().preflight).toBeNull()
      expect(useFineTuningStore.getState().loading).toBe(false)
      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
    })

    it('runPreflightCheck sets preflight on success', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune/preflight', () =>
          HttpResponse.json(apiSuccess(BASE_PREFLIGHT)),
        ),
      )

      await useFineTuningStore.getState().runPreflightCheck({ source_dir: 'data' })

      expect(useFineTuningStore.getState().preflight).toEqual(BASE_PREFLIGHT)
      expect(useFineTuningStore.getState().loading).toBe(false)
    })

    it('deployCheckpointAction emits error toast on failure', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune/checkpoints/:id/deploy', () =>
          HttpResponse.json(apiError('Deploy failed'), { status: 500 }),
        ),
      )

      await useFineTuningStore.getState().deployCheckpointAction('ckpt-1')

      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to deploy checkpoint')
    })

    it('rollbackCheckpointAction emits error toast on failure', async () => {
      server.use(
        http.post('/api/v1/admin/memory/fine-tune/checkpoints/:id/rollback', () =>
          HttpResponse.json(apiError('Rollback failed'), { status: 500 }),
        ),
      )

      await useFineTuningStore.getState().rollbackCheckpointAction('ckpt-1')

      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to rollback checkpoint')
    })

    it('deleteCheckpointAction emits error toast on failure', async () => {
      server.use(
        http.delete('/api/v1/admin/memory/fine-tune/checkpoints/:id', () =>
          HttpResponse.json(apiError('Delete failed'), { status: 500 }),
        ),
      )

      await useFineTuningStore.getState().deleteCheckpointAction('ckpt-1')

      expect(
        selectFineTuningBannerError(useFineTuningStore.getState().errors),
      ).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to delete checkpoint')
    })
  })
})
