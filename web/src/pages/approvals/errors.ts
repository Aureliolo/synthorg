/**
 * User-facing error / hint strings for the approvals workflow.
 *
 * The rejection-reason validation hint is duplicated across the
 * approval drawer (real-time character counter + form-submit guard)
 * and the ApprovalsPage list-level reject action; centralising the
 * literal here keeps the wording in lockstep when a future copy
 * tweak lands.
 */
export const REJECTION_REASON_REQUIRED =
  'Rejection requires a reason for the approval record. Provide a brief explanation.'
