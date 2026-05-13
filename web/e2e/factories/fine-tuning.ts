/**
 * Fine-tuning pipeline mock-data builders.
 */

export type FineTuneStage =
  | 'idle'
  | 'generating_data'
  | 'mining_negatives'
  | 'training'
  | 'evaluating'
  | 'deploying'
  | 'complete'
  | 'failed'

export interface MockFineTuneStatus {
  run_id: string | null
  stage: FineTuneStage
  progress: number | null
  error: string | null
}

export function makeFineTuneStatus(
  overrides: Partial<MockFineTuneStatus> = {},
): MockFineTuneStatus {
  return {
    run_id: null,
    stage: 'idle',
    progress: null,
    error: null,
    ...overrides,
  }
}

export interface MockFineTuneRun {
  id: string
  stage: FineTuneStage
  progress: number | null
  error: string | null
  started_at: string
  updated_at: string
  completed_at: string | null
  duration_seconds: number | null
  stages_completed: readonly string[]
  config: Record<string, unknown>
}

export function makeFineTuneRun(
  overrides: Partial<MockFineTuneRun> = {},
): MockFineTuneRun {
  return {
    id: 'run-001',
    stage: 'training',
    progress: 0.42,
    error: null,
    started_at: '2026-05-13T10:00:00Z',
    updated_at: '2026-05-13T10:05:00Z',
    completed_at: null,
    duration_seconds: null,
    stages_completed: ['generating_data'],
    config: {},
    ...overrides,
  }
}
