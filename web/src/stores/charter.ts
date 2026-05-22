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

// Charter content lives in in-memory Zustand state for the lifetime of
// the tab; nothing is persisted to localStorage / sessionStorage. The
// authoritative copy is the server-side charter; closing the tab loses
// only unsent draft edits.

/** One rendered turn in the local interview transcript. */
export interface InterviewMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
}

interface CharterState {
  // List view
  charters: ProjectCharter[]
  loading: boolean
  error: string | null
  // Opaque cursor for the NEXT page; ``null`` when the catalogue end
  // has been reached. Stores keep ``nextCursor`` + ``hasMore`` rather
  // than offset arithmetic per the cursor-pagination MANDATORY rule
  // in ``web/CLAUDE.md``.
  nextCursor: string | null
  hasMore: boolean

  // Active interview
  conversationId: string | null
  messages: InterviewMessage[]
  draftCharter: ProjectCharter | null
  sending: boolean
  conversationClosed: boolean

  // Actions
  fetchCharters: (filters?: CharterFilters) => Promise<void>
  fetchMoreCharters: (filters?: CharterFilters) => Promise<void>
  runTurn: (message: string) => Promise<void>
  editDraft: (id: string, data: CharterEditRequest) => Promise<ProjectCharter | null>
  approve: (id: string) => Promise<CharterApprovalResult | null>
  cancel: (id: string) => Promise<boolean>
  resetInterview: () => void
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

  fetchCharters: async (filters) => {
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
  },

  fetchMoreCharters: async (filters) => {
    const { hasMore, nextCursor, charters, loading } = get()
    // Early-return on no-more-pages and on duplicate-in-flight calls per
    // the cursor-pagination MANDATORY rule in ``web/CLAUDE.md``.
    if (!hasMore || !nextCursor || loading) return
    set({ loading: true })
    try {
      const page = await charterApi.listCharters({
        ...filters,
        cursor: nextCursor,
      })
      set({
        charters: [...charters, ...page.data],
        nextCursor: page.nextCursor,
        hasMore: page.hasMore,
        loading: false,
      })
    } catch (err) {
      log.warn('Failed to fetch more charters', sanitizeForLog(err))
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  runTurn: async (message) => {
    // Refuse re-entry while a turn is in flight. Overlapping turns would
    // share the same ``previousMessages`` snapshot, so a single error
    // could roll the transcript back over a newer turn's optimistic
    // user bubble and assistant reply.
    if (get().sending) return
    const { conversationId, messages: previousMessages } = get()
    // Snapshot the pre-turn transcript so we can roll the optimistic
    // user bubble back if the API call fails (otherwise the user sees
    // their message with no assistant reply).
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
        content:
          result.status === 'needs_more'
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
      // Restore the pre-turn transcript so a failed send does not leave
      // an orphan user bubble in the chat.
      set({ sending: false, messages: previousMessages })
    }
  },

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
