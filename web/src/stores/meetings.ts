import { create } from 'zustand'
import {
  _resetRequestSeqs,
  bumpRequestEpoch,
} from './meetings/_state'
import { createCrudActions } from './meetings/crud-actions'
import { createWsHandler } from './meetings/ws-handler'
import type { MeetingsState } from './meetings/types'

export type { MeetingsState } from './meetings/types'
export { _resetRequestSeqs }

export const useMeetingsStore = create<MeetingsState>()((set, get) => ({
  meetings: [],
  selectedMeeting: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  detailError: null,
  triggering: false,
  deleting: false,

  ...createCrudActions(set, get),
  ...createWsHandler(set, get),

  dispose: () => {
    // Bump the generation token so any in-flight request from
    // before the dispose can never collide with post-dispose seq
    // values (the captured ``epoch`` will not match the new
    // ``requestEpoch``). Resetting the seq counters keeps fresh
    // calls starting from zero.
    bumpRequestEpoch()
    _resetRequestSeqs()
  },
}))
