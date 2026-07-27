import type { FineTuneDataSourceType, FineTuneRequest } from '@/api/types/fine-tuning'

export interface PipelineFormState {
  sourceDir: string
  dataSource: FineTuneDataSourceType
  epochs: string
  learningRate: string
  effectiveBatchSize: string
  showAdvanced: boolean
}

export function buildStartRequest(state: PipelineFormState): FineTuneRequest {
  // The generated request type is fully ``readonly``, so assemble the
  // optional advanced fields up front and spread them rather than mutating.
  const advanced = state.showAdvanced
  const epochs = parsePositive(advanced ? state.epochs : '')
  const learningRate = parsePositive(advanced ? state.learningRate : '')
  const batchSize = parsePositive(advanced ? state.effectiveBatchSize : '')
  return {
    source_dir: state.sourceDir,
    data_source: state.dataSource,
    ...(epochs !== null && { epochs }),
    ...(learningRate !== null && { learning_rate: learningRate }),
    ...(batchSize !== null && { batch_size: batchSize }),
  }
}

function parsePositive(input: string): number | null {
  if (input === '') return null
  const parsed = Number(input)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}
