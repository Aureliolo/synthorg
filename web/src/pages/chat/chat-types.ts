import type { ExecutedToolCall } from '@/api/types'

/** One rendered turn in the Chief of Staff explain-only transcript. */
export interface ChiefOfStaffMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  confidence?: number
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean
}

/** A parked work item, with its approval id for a deep link. */
export interface RequestWorkProposal {
  title: string
  approvalId: string
}

/** A parked steering directive, with its approval id for a deep link. */
export interface RequestWorkSteering {
  text: string
  approvalId: string
}

/** One rendered turn in the clarify-and-propose transcript. */
export interface RequestWorkMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  /** Role of the routed agent that answered, when concern-routed. */
  responderRole?: string | undefined
  /** Display name of the routed agent, when concern-routed. */
  responderName?: string | undefined
  /** Concern topic that selected the role, when routed. */
  routedTopic?: string | undefined
  /** Parked work items, on the "proposed" branch. */
  proposals?: readonly RequestWorkProposal[] | undefined
  /** Parked steering directives, on the "proposed" branch. */
  steering?: readonly RequestWorkSteering[] | undefined
  /** Renders as a distinct error notice (not a normal assistant reply). */
  isError?: boolean | undefined
}

/** One rendered turn in the multi-agent group transcript. */
export interface GroupMessage {
  id: number
  /** ``human`` = the operator's turn, ``agent`` = an attributed
   *  contribution, ``notice`` = a system line (truncation / failure),
   *  ``invite`` = an agent-initiated invite awaiting human consent. */
  kind: 'human' | 'agent' | 'notice' | 'invite'
  /** Bubble body. For ``invite`` bubbles this is the stated reason. */
  content: string
  /** Attributed agent name, on ``agent`` bubbles. */
  agentName?: string | undefined
  /** Attributed agent role, on ``agent`` bubbles. */
  role?: string | undefined
  /** Inviting agent's name, on ``invite`` bubbles. */
  requestedByName?: string | undefined
  /** Invite target's name, on ``invite`` bubbles. */
  targetName?: string | undefined
  /** Invite target's role, on ``invite`` bubbles (``undefined`` when the
   *  target was named directly rather than by role). */
  targetRole?: string | undefined
  /** Backing approval id, on ``invite`` bubbles: the in-context
   *  Approve/Reject buttons resolve this approval. */
  approvalId?: string | undefined
  /** Set once the operator resolves an ``invite`` in context. The
   *  invited agent joins on the next round after ``approved``. */
  resolved?: 'approved' | 'declined'
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean
}

/** One rendered turn in the direct-action transcript. */
export interface ActMessage {
  id: number
  /** ``human`` = the operator's instruction, ``action`` = the agent's
   *  outcome (executed tools + message, or a parked approval),
   *  ``notice`` = a system line (request failure). */
  kind: 'human' | 'action' | 'notice'
  /** Bubble body: the instruction, the agent's final message, or a notice. */
  content: string
  /** Acting agent's name, on ``action`` bubbles. */
  agentName?: string | undefined
  /** Acting agent's role, on ``action`` bubbles (resolved from the roster). */
  agentRole?: string | undefined
  /** Tools the action executed, on ``action`` bubbles. */
  toolCalls?: readonly ExecutedToolCall[] | undefined
  /** Approval id, on ``action`` bubbles when the action parked for consent. */
  parkedApprovalId?: string | undefined
  /** Renders the notice as a distinct error state with a Try-again. */
  isError?: boolean
}
