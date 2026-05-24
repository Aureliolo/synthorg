import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { FineTuneStatus } from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'
import { VALID_STAGES } from './_helpers'
import type { FineTuningGet, FineTuningSet } from './types'

const log = createLogger('fine-tuning-store')

interface ParsedStageProgress {
  stage: FineTuneStatus['stage']
  progress: number | null
  runId: string | null
}

function resolveStage(
  rawStage: string | undefined,
  currentStatus: FineTuneStatus | null,
): FineTuneStatus['stage'] {
  if (rawStage != null && VALID_STAGES.has(rawStage)) {
    return rawStage as FineTuneStatus['stage']
  }
  return currentStatus?.stage ?? 'idle'
}

function clampProgress(raw: number | undefined): number | null {
  if (raw == null) return null
  return Math.min(1, Math.max(0, raw))
}

function parseStageAndProgress(
  data: Record<string, unknown>,
  currentStatus: FineTuneStatus | null,
): ParsedStageProgress {
  return {
    stage: resolveStage(data.stage as string | undefined, currentStatus),
    progress: clampProgress(data.progress as number | undefined),
    runId: (data.run_id as string) ?? currentStatus?.run_id ?? null,
  }
}

function applyProgress(set: FineTuningSet, parsed: ParsedStageProgress): void {
  set({
    status: {
      run_id: parsed.runId,
      stage: parsed.stage,
      progress: parsed.progress,
      error: null,
    },
  })
}

function applyStageChanged(
  set: FineTuningSet,
  parsed: ParsedStageProgress,
): void {
  set({
    status: {
      run_id: parsed.runId,
      stage: parsed.stage,
      progress: 0,
      error: null,
    },
  })
}

function refreshAllAfterTerminalEvent(get: FineTuningGet): void {
  // Refresh all data on completion/failure. Each fetch sets its own
  // ``error`` slot on the store so the page banner picks refetch
  // failures up; no toast (WS-driven refetches are not user-initiated
  // actions).
  get().fetchStatus().catch((err: unknown) => {
    log.warn('fine-tune ws fetchStatus failed', sanitizeForLog(err))
  })
  get().fetchCheckpoints().catch((err: unknown) => {
    log.warn('fine-tune ws fetchCheckpoints failed', sanitizeForLog(err))
  })
  get().fetchRuns().catch((err: unknown) => {
    log.warn('fine-tune ws fetchRuns failed', sanitizeForLog(err))
  })
}

function handleWsEventImpl(
  set: FineTuningSet,
  get: FineTuningGet,
  event: WsEvent,
): void {
  const { event_type: eventType, payload: data } = event
  if (!eventType.startsWith('memory.fine_tune.')) return
  const parsed = parseStageAndProgress(data, get().status)
  if (eventType === 'memory.fine_tune.progress') {
    applyProgress(set, parsed)
    return
  }
  if (eventType === 'memory.fine_tune.stage_changed') {
    applyStageChanged(set, parsed)
    return
  }
  if (
    eventType === 'memory.fine_tune.completed'
    || eventType === 'memory.fine_tune.failed'
  ) {
    refreshAllAfterTerminalEvent(get)
  }
}

export function createWsHandler(set: FineTuningSet, get: FineTuningGet) {
  return {
    handleWsEvent: (event: WsEvent) => handleWsEventImpl(set, get, event),
  }
}
