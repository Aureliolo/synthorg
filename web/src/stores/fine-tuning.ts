import { create } from 'zustand'
import {
  NO_ERRORS,
  NO_MORE,
  selectFineTuningBannerError,
} from './fine-tuning/_helpers'
import { createCrudActions } from './fine-tuning/crud-actions'
import { createFetchActions } from './fine-tuning/fetch-actions'
import { createWsHandler } from './fine-tuning/ws-handler'
import type {
  FineTuningErrors,
  FineTuningState,
} from './fine-tuning/types'

export type { FineTuningErrors, FineTuningState }
export { selectFineTuningBannerError }

export const useFineTuningStore = create<FineTuningState>((set, get) => ({
  status: null,
  checkpoints: [],
  checkpointsPagination: NO_MORE,
  runs: [],
  runsPagination: NO_MORE,
  preflight: null,
  loading: false,
  errors: NO_ERRORS,

  ...createFetchActions(set),
  ...createCrudActions(set, get),
  ...createWsHandler(set, get),
}))
