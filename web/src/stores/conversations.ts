import { create } from 'zustand'

import type { ConversationParticipant } from '@/api/types'
import type {
  ActMessage,
  ChiefOfStaffMessage,
  GroupMessage,
  RequestWorkMessage,
} from '@/pages/chat/chat-types'
import type { ChatScopeValue } from '@/pages/chat/ChatScopePicker'

/**
 * In-memory transcript store for the five conversational modes.
 *
 * Hoisting the transcript out of each mode panel's local state fixes the
 * data-loss bug where switching modes (which unmounts the panel) discarded
 * the conversation: the panels are now thin selectors over this store, so a
 * remount re-reads the same transcript. Modelled on ``stores/charter.ts``:
 * plain ``create`` with NO ``persist`` and NO client storage, so the
 * dashboard stays a pure API consumer -- the store is hydrated only from
 * POST responses (or, later, a resume GET) and never survives a reload.
 */

interface StaffSlice {
  messages: readonly ChiefOfStaffMessage[]
  /** Proposal/alert the conversation is scoped to; persists across turns. */
  scope: ChatScopeValue | null
}

interface WorkSlice {
  messages: readonly RequestWorkMessage[]
  conversationId: string | undefined
  /** True once the backend closes the conversation; the input is frozen. */
  closed: boolean
}

interface GroupSlice {
  messages: readonly GroupMessage[]
  conversationId: string | undefined
  roster: readonly ConversationParticipant[]
  selectedIds: readonly string[]
  started: boolean
}

interface ActionSlice {
  messages: readonly ActMessage[]
  conversationId: string | undefined
  selectedAgentId: string | null
}

type SlicePatch<S> = Partial<S> | ((slice: S) => Partial<S>)

function resolvePatch<S>(patch: SlicePatch<S>, slice: S): Partial<S> {
  return typeof patch === 'function' ? patch(slice) : patch
}

export interface ConversationsState {
  staff: StaffSlice
  work: WorkSlice
  group: GroupSlice
  action: ActionSlice
  setStaff: (patch: SlicePatch<StaffSlice>) => void
  setWork: (patch: SlicePatch<WorkSlice>) => void
  setGroup: (patch: SlicePatch<GroupSlice>) => void
  setAction: (patch: SlicePatch<ActionSlice>) => void
  /** Clear every mode's transcript (test teardown; new-session reset). */
  resetAll: () => void
}

function initialSlices(): Pick<
  ConversationsState,
  'staff' | 'work' | 'group' | 'action'
> {
  return {
    staff: { messages: [], scope: null },
    work: { messages: [], conversationId: undefined, closed: false },
    group: {
      messages: [],
      conversationId: undefined,
      roster: [],
      selectedIds: [],
      started: false,
    },
    action: { messages: [], conversationId: undefined, selectedAgentId: null },
  }
}

export const useConversationsStore = create<ConversationsState>()((set) => ({
  ...initialSlices(),
  setStaff: (patch) =>
    set((st) => ({ staff: { ...st.staff, ...resolvePatch(patch, st.staff) } })),
  setWork: (patch) =>
    set((st) => ({ work: { ...st.work, ...resolvePatch(patch, st.work) } })),
  setGroup: (patch) =>
    set((st) => ({ group: { ...st.group, ...resolvePatch(patch, st.group) } })),
  setAction: (patch) =>
    set((st) => ({
      action: { ...st.action, ...resolvePatch(patch, st.action) },
    })),
  resetAll: () => set(initialSlices()),
}))
