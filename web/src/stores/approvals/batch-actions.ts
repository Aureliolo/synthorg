import * as approvalsApi from '@/api/endpoints/approvals'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import {
  formatBatchErrors,
  getCrudErrorTitle,
  getErrorMessage,
} from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { MAX_BATCH_SIZE } from './_state'
import type { ApprovalsGet } from './types'

const log = createLogger('approvals')

interface BatchOutcome {
  succeeded: number
  failed: number
  failedReasons: string[]
}

type ApprovalApiCall = (id: string) => Promise<unknown>

interface BatchRunArgs {
  ids: string[]
  call: ApprovalApiCall
  rollbackFor: (id: string) => () => void
}

async function runBatch(
  get: ApprovalsGet,
  { ids, call, rollbackFor }: BatchRunArgs,
): Promise<BatchOutcome> {
  if (ids.length > MAX_BATCH_SIZE) {
    return {
      succeeded: 0,
      failed: ids.length,
      failedReasons: [`Batch size exceeds maximum of ${MAX_BATCH_SIZE}`],
    }
  }

  const rollbacks = new Map<string, () => void>()
  for (const id of ids) {
    rollbacks.set(id, rollbackFor(id))
  }

  const results = await Promise.allSettled(ids.map((id) => call(id)))

  let succeeded = 0
  let failed = 0
  const failedReasons: string[] = []
  for (let i = 0; i < results.length; i++) {
    const result = results[i]!
    const id = ids[i]!
    if (result.status === 'fulfilled') {
      get().upsertApproval(result.value as Awaited<ReturnType<typeof approvalsApi.approveApproval>>)
      succeeded++
    } else {
      const rollback = rollbacks.get(id)
      if (rollback) rollback()
      failedReasons.push(getErrorMessage(result.reason))
      failed++
    }
  }

  if (failed === 0) {
    get().clearSelection()
  }
  return { succeeded, failed, failedReasons }
}

function emitBatchOutcomeToast(
  outcome: BatchOutcome,
  successTitle: string,
  failureTitle: string,
  sentinelErr: Error,
): void {
  if (outcome.failed === 0 && outcome.succeeded > 0) {
    useToastStore.getState().add({ variant: 'success', title: successTitle })
    return
  }
  if (outcome.failed === 0) return
  const description = outcome.failedReasons.length > 0
    ? formatBatchErrors(outcome.failedReasons)
    : undefined
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(sentinelErr, failureTitle),
    description,
  })
}

function totalFailureOutcome(
  ids: readonly string[],
  err: unknown,
): BatchOutcome {
  return {
    succeeded: 0,
    failed: ids.length,
    failedReasons: [getErrorMessage(err)],
  }
}

export function createBatchActions(get: ApprovalsGet) {
  return {
    async batchApprove(ids: string[], comment?: string): Promise<BatchOutcome> {
      // Outer try/catch so any unexpected throw from runBatch / its
      // rollback callbacks still produces the canonical BatchOutcome
      // sentinel instead of leaking a rejected promise to the caller.
      let outcome: BatchOutcome
      try {
        outcome = await runBatch(get, {
          ids,
          call: (id) =>
            approvalsApi.approveApproval(id, comment ? { comment } : undefined),
          rollbackFor: (id) => get().optimisticApprove(id),
        })
      } catch (err) {
        log.error(
          'Batch approve failed unexpectedly',
          sanitizeForLog({ error: err }),
        )
        outcome = totalFailureOutcome(ids, err)
      }
      if (outcome.failed > 0) {
        log.error('Batch approve failed', sanitizeForLog({
          failed: outcome.failed,
          reasons: outcome.failedReasons,
        }))
      }
      const sentinel = new Error(
        outcome.failedReasons[0] ?? 'Batch approve failed',
      )
      emitBatchOutcomeToast(
        outcome,
        outcome.succeeded === 1
          ? 'Approval granted'
          : `${String(outcome.succeeded)} approvals granted`,
        outcome.failed === 1
          ? 'Could not approve'
          : `${String(outcome.failed)} approvals failed`,
        sentinel,
      )
      return outcome
    },

    async batchReject(ids: string[], reason: string): Promise<BatchOutcome> {
      let outcome: BatchOutcome
      try {
        outcome = await runBatch(get, {
          ids,
          call: (id) => approvalsApi.rejectApproval(id, { reason }),
          rollbackFor: (id) => get().optimisticReject(id),
        })
      } catch (err) {
        log.error(
          'Batch reject failed unexpectedly',
          sanitizeForLog({ error: err }),
        )
        outcome = totalFailureOutcome(ids, err)
      }
      if (outcome.failed > 0) {
        log.error('Batch reject failed', sanitizeForLog({
          failed: outcome.failed,
          reasons: outcome.failedReasons,
        }))
      }
      const sentinel = new Error(
        outcome.failedReasons[0] ?? 'Batch reject failed',
      )
      emitBatchOutcomeToast(
        outcome,
        outcome.succeeded === 1
          ? 'Approval rejected'
          : `${String(outcome.succeeded)} approvals rejected`,
        outcome.failed === 1
          ? 'Could not reject'
          : `${String(outcome.failed)} approvals failed`,
        sentinel,
      )
      return outcome
    },
  }
}
