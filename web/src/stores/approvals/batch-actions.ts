import * as approvalsApi from '@/api/endpoints/approvals'
import { getErrorMessage } from '@/utils/errors'
import { MAX_BATCH_SIZE } from './_state'
import type { ApprovalsGet } from './types'

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

export function createBatchActions(get: ApprovalsGet) {
  return {
    async batchApprove(ids: string[], comment?: string): Promise<BatchOutcome> {
      return runBatch(get, {
        ids,
        call: (id) =>
          approvalsApi.approveApproval(id, comment ? { comment } : undefined),
        rollbackFor: (id) => get().optimisticApprove(id),
      })
    },

    async batchReject(ids: string[], reason: string): Promise<BatchOutcome> {
      return runBatch(get, {
        ids,
        call: (id) => approvalsApi.rejectApproval(id, { reason }),
        rollbackFor: (id) => get().optimisticReject(id),
      })
    },
  }
}
