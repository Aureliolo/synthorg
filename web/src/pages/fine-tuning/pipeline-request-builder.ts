import type { StartFineTuneRequest } from '@/api/endpoints/fine-tuning'

export interface PipelineFormState {
  sourceDir: string
  epochs: string
  learningRate: string
  effectiveBatchSize: string
  showAdvanced: boolean
}

export function buildStartRequest(state: PipelineFormState): StartFineTuneRequest {
  const request: StartFineTuneRequest = { source_dir: state.sourceDir }
  if (!state.showAdvanced) return request
  applyPositiveNumber(state.epochs, (value) => {
    request.epochs = value
  })
  applyPositiveNumber(state.learningRate, (value) => {
    request.learning_rate = value
  })
  applyPositiveNumber(state.effectiveBatchSize, (value) => {
    request.batch_size = value
  })
  return request
}

function applyPositiveNumber(input: string, apply: (value: number) => void): void {
  if (input === '') return
  const parsed = Number(input)
  if (Number.isFinite(parsed) && parsed > 0) apply(parsed)
}
