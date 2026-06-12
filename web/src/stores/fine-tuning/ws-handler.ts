import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import type { FineTuneStatus } from '@/api/endpoints/fine-tuning'
import type { WsEvent } from '@/api/types/websocket'
import { VALID_STAGE_VALUES } from './_helpers'
import type { FineTuningGet, FineTuningSet } from './types'

const log = createLogger('fine-tuning-store')

interface ParsedStageProgress {
  stage: FineTuneStatus['stage']
  progress: number | null
  runId: string | null
}

function clampProgress(raw: unknown): number | null {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return Math.min(1, Math.max(0, raw))
}

function parseStageAndProgress(
  data: Record<string, unknown>,
  currentStatus: FineTuneStatus | null,
): ParsedStageProgress {
  const stage = sanitizeWsEnum<FineTuneStatus['stage']>(
    data['stage'],
    VALID_STAGE_VALUES,
    currentStatus?.stage ?? 'idle',
    { field: 'memory.fine_tune.stage' },
  )
  const sanitizedRunId = sanitizeWsString(data['run_id'], 128)
  return {
    stage,
    progress: clampProgress(data['progress']),
    runId: sanitizedRunId ?? currentStatus?.run_id ?? null,
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
