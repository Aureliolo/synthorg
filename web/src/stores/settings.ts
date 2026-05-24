import { create } from 'zustand'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { createFetchActions } from './settings/fetch-actions'
import { createMutationActions } from './settings/mutation-actions'
import type { SettingsState } from './settings/types'
import type { WsEvent } from '@/api/types/websocket'

export type { SettingsState } from './settings/types'

const log = createLogger('settings')

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  currency: DEFAULT_CURRENCY,
  schema: [],
  entries: [],
  loading: false,
  error: null,
  savingKeys: new Map(),
  appliedMutationTokens: new Map(),
  entriesGeneration: 0,
  saveError: null,

  ...createFetchActions(set, get),
  ...createMutationActions(set, get),

  updateFromWsEvent: (event: WsEvent) => {
    if (event.channel === 'system') {
      void get().refreshEntries().catch((err: unknown) => {
        log.warn('WebSocket-triggered refresh failed', {
          error: sanitizeForLog(getErrorMessage(err)),
        })
      })
    }
  },
}))
