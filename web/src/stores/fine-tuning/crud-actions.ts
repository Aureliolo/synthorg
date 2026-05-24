import {
  cancelFineTune,
  deleteCheckpoint,
  deployCheckpoint,
  rollbackCheckpoint,
  runPreflight,
  startFineTune,
} from '@/api/endpoints/fine-tuning'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type { StartFineTuneRequest } from '@/api/endpoints/fine-tuning'
import type { FineTuningGet, FineTuningSet } from './types'

const log = createLogger('fine-tuning-store')

function emitMutationError(
  err: unknown,
  fallbackTitle: string,
  logPrefix: string,
): void {
  log.error(logPrefix, sanitizeForLog(err))
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(err, fallbackTitle),
    description: getErrorMessage(err),
  })
}

async function startRunImpl(
  set: FineTuningSet,
  request: StartFineTuneRequest,
): Promise<void> {
  set({ loading: true })
  try {
    const status = await startFineTune(request)
    set({ status, loading: false })
  } catch (err) {
    set({ loading: false })
    emitMutationError(
      err,
      'Failed to start fine-tune run',
      'Failed to start fine-tune run',
    )
  }
}

async function cancelRunImpl(set: FineTuningSet): Promise<void> {
  try {
    const status = await cancelFineTune()
    set({ status })
  } catch (err) {
    emitMutationError(
      err,
      'Failed to cancel fine-tune run',
      'Failed to cancel fine-tune run',
    )
  }
}

async function runPreflightImpl(
  set: FineTuningSet,
  request: StartFineTuneRequest,
): Promise<void> {
  set({ loading: true, preflight: null })
  try {
    const result = await runPreflight(request)
    set({ preflight: result, loading: false })
  } catch (err) {
    set({ loading: false })
    emitMutationError(err, 'Preflight check failed', 'Failed to run preflight')
  }
}

async function checkpointMutationImpl(
  get: FineTuningGet,
  call: () => Promise<unknown>,
  fallbackTitle: string,
  logPrefix: string,
): Promise<void> {
  try {
    await call()
    await get().fetchCheckpoints()
  } catch (err) {
    emitMutationError(err, fallbackTitle, logPrefix)
  }
}

export function createCrudActions(
  set: FineTuningSet,
  get: FineTuningGet,
) {
  return {
    startRun: (request: StartFineTuneRequest) =>
      startRunImpl(set, request),
    cancelRun: () => cancelRunImpl(set),
    runPreflightCheck: (request: StartFineTuneRequest) =>
      runPreflightImpl(set, request),
    deployCheckpointAction: (id: string) =>
      checkpointMutationImpl(
        get,
        () => deployCheckpoint(id),
        'Failed to deploy checkpoint',
        'Failed to deploy checkpoint',
      ),
    rollbackCheckpointAction: (id: string) =>
      checkpointMutationImpl(
        get,
        () => rollbackCheckpoint(id),
        'Failed to rollback checkpoint',
        'Failed to rollback checkpoint',
      ),
    deleteCheckpointAction: (id: string) =>
      checkpointMutationImpl(
        get,
        () => deleteCheckpoint(id),
        'Failed to delete checkpoint',
        'Failed to delete checkpoint',
      ),
  }
}
