import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import * as charterApi from '@/api/endpoints/charter'
import type { CharterFilters } from '@/api/endpoints/charter'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  CharterApprovalResult,
  CharterEditRequest,
  ProjectCharter,
} from '@/api/types'

const log = createLogger('charter')

/** One rendered turn in the local interview transcript. */
export interface InterviewMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
}

interface CharterState {
  charters: ProjectCharter[]
  loading: boolean
  error: string | null
  nextCursor: string | null
  hasMore: boolean

  conversationId: string | null
  messages: InterviewMessage[]
  draftCharter: ProjectCharter | null
  sending: boolean
  conversationClosed: boolean

  fetchCharters: (filters?: CharterFilters) => Promise<void>
  fetchMoreCharters: (filters?: CharterFilters) => Promise<void>
  runTurn: (message: string) => Promise<void>
  editDraft: (
    id: string,
    data: CharterEditRequest,
  ) => Promise<ProjectCharter | null>
  approve: (id: string) => Promise<CharterApprovalResult | null>
  cancel: (id: string) => Promise<boolean>
  resetInterview: () => void
}

type CharterSet = StoreApi<CharterState>['setState']
type CharterGet = StoreApi<CharterState>['getState']

async function fetchChartersImpl(
  set: CharterSet,
  filters?: CharterFilters,
): Promise<void> {
  set({ loading: true, error: null })
  try {
    const page = await charterApi.listCharters(filters)
    set({
      charters: page.data,
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    })
  } catch (err) {
    log.warn('Failed to fetch charters', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchMoreChartersImpl(
  set: CharterSet,
  get: CharterGet,
  filters?: CharterFilters,
): Promise<void> {
  const { hasMore, nextCursor, loading } = get()
  if (!hasMore || !nextCursor || loading) return
  set({ loading: true })
  try {
    const page = await charterApi.listCharters({
      ...filters,
      cursor: nextCursor,
    })
    set((state) => ({
      charters: [...state.charters, ...page.data],
      nextCursor: page.nextCursor,
      hasMore: page.hasMore,
      loading: false,
    }))
  } catch (err) {
    log.warn('Failed to fetch more charters', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function runTurnImpl(
  set: CharterSet,
  get: CharterGet,
  message: string,
): Promise<void> {
  if (get().sending) return
  const { conversationId, messages: previousMessages } = get()
  set({
    sending: true,
    messages: [
      ...previousMessages,
      { id: crypto.randomUUID(), role: 'user', content: message },
    ],
  })
  try {
    const result = await charterApi.runInterviewTurn({
      message,
      conversation_id: conversationId,
      project: null,
    })
    const reply: InterviewMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: result.status === 'needs_more'
        ? result.next_question ?? ''
        : 'Charter drafted. Review and edit it, then approve to start the run.',
    }
    set((s) => ({
      sending: false,
      conversationId: result.conversation_id,
      messages: [...s.messages, reply],
      draftCharter: result.charter ?? s.draftCharter,
      conversationClosed: result.conversation_closed,
    }))
  } catch (err) {
    log.error('Interview turn failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Could not continue the interview',
      description: getErrorMessage(err),
    })
    set({ sending: false, messages: previousMessages })
  }
}

export const useCharterStore = create<CharterState>()((set, get) => ({
  charters: [],
  loading: false,
  error: null,
  nextCursor: null,
  hasMore: false,
  conversationId: null,
  messages: [],
  draftCharter: null,
  sending: false,
  conversationClosed: false,

  fetchCharters: (filters) => fetchChartersImpl(set, filters),
  fetchMoreCharters: (filters) => fetchMoreChartersImpl(set, get, filters),
  runTurn: (message) => runTurnImpl(set, get, message),

  editDraft: async (id, data) => {
    try {
      const updated = await charterApi.editCharter(id, data)
      set({ draftCharter: updated })
      useToastStore.getState().add({ variant: 'success', title: 'Charter updated' })
      return updated
    } catch (err) {
      log.error('Charter edit failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not update the charter',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  approve: async (id) => {
    try {
      const result = await charterApi.approveCharter(id)
      set({ draftCharter: result.charter, conversationClosed: true })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Charter approved',
        description: 'The project run has started.',
      })
      return result
    } catch (err) {
      log.error('Charter approval failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not approve the charter',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  cancel: async (id) => {
    try {
      const cancelled = await charterApi.cancelCharter(id)
      set({ draftCharter: cancelled, conversationClosed: true })
      useToastStore.getState().add({ variant: 'success', title: 'Charter cancelled' })
      return true
    } catch (err) {
      log.error('Charter cancel failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not cancel the charter',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  resetInterview: () => {
    set({
      conversationId: null,
      messages: [],
      draftCharter: null,
      sending: false,
      conversationClosed: false,
    })
  },
}))
