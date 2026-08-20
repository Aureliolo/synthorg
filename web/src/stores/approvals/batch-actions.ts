import * as approvalsApi from '@/api/endpoints/approvals'
import type { ApprovalResponse } from '@/api/types/approvals'
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

type ApprovalApiCall = (id: string) => Promise<ApprovalResponse>

interface BatchRunArgs {
  ids: string[]
  call: ApprovalApiCall
  rollbackFor: (id: string) => () => void
}

/**
 * Apply one settled call: upsert on success, roll back + report on failure.
 * Returns the failure reason, or `null` when the call succeeded.
 */
function applySettledResult(
  get: ApprovalsGet,
  rollbacks: Map<string, () => void>,
  result: PromiseSettledResult<ApprovalResponse>,
  id: string,
): string | null {
  if (result.status === 'fulfilled') {
    get().upsertApproval(result.value)
    return null
  }
  rollbacks.get(id)?.()
  return getErrorMessage(result.reason)
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
  const failedReasons: string[] = []
  for (let i = 0; i < results.length; i++) {
    const result = results[i]
    const id = ids[i]
    if (result === undefined || id === undefined) continue
    const reason = applySettledResult(get, rollbacks, result, id)
    if (reason === null) {
      succeeded++
    } else {
      failedReasons.push(reason)
    }
  }

  const failed = failedReasons.length
  if (failed === 0) {
    get().clearSelection()
  }
  return { succeeded, failed, failedReasons }
}

/** What the toast says for each of the three ways a batch can end. */
interface BatchToastLabels {
  readonly success: string
  /** Some landed and some did not; must name both counts. */
  readonly partial: string
  readonly failure: string
}

/** Per-verb wording; `batchLabels` fills in the counts. */
const BATCH_VERB_WORDING = {
  approve: {
    settled: 'granted',
    settledTitle: 'Granted',
    refused: 'Could not approve',
  },
  reject: {
    settled: 'rejected',
    settledTitle: 'Rejected',
    refused: 'Could not reject',
  },
} as const

function batchLabels(
  verb: keyof typeof BATCH_VERB_WORDING,
  outcome: BatchOutcome,
  total: number,
): BatchToastLabels {
  const words = BATCH_VERB_WORDING[verb]
  return {
    success: outcome.succeeded === 1
      ? `Approval ${words.settled}`
      : `${String(outcome.succeeded)} approvals ${words.settled}`,
    partial: `${words.settledTitle} ${String(outcome.succeeded)} of ${String(total)}`,
    failure: outcome.failed === 1
      ? words.refused
      : `${String(outcome.failed)} approvals failed`,
  }
}

function emitBatchOutcomeToast(
  outcome: BatchOutcome,
  labels: BatchToastLabels,
  sentinelErr: Error,
): void {
  if (outcome.failed === 0 && outcome.succeeded > 0) {
    useToastStore.getState().add({ variant: 'success', title: labels.success })
    return
  }
  if (outcome.failed === 0) return
  const description = outcome.failedReasons.length > 0
    ? formatBatchErrors(outcome.failedReasons)
    : undefined
  // A partial batch is neither outcome, and reporting it as the failure was
  // the whole of what the operator saw: `Reject 2` settled one and failed the
  // other, and the single error toast let them conclude nothing had happened
  // on an action the modal calls irreversible. The projects list already
  // reports "Deleted N of M"; this is the same answer for this queue.
  if (outcome.succeeded > 0) {
    useToastStore.getState().add({
      variant: 'warning',
      title: labels.partial,
      description,
    })
    return
  }
  useToastStore.getState().add({
    variant: 'error',
    ...getCrudErrorTitle(sentinelErr, labels.failure),
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
        batchLabels('approve', outcome, ids.length),
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
        batchLabels('reject', outcome, ids.length),
        sentinel,
      )
      return outcome
    },
  }
}
