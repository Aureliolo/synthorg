import { create } from 'zustand'
import { createAgentsSlice } from './agents'
import { createCompanySlice } from './company'
import { createCompletionSlice } from './completion'
import { createNavigationSlice } from './navigation'
import { createProvidersSlice } from './providers'
import { createTemplateSlice } from './template'
import type { SetupWizardState } from './types'

export type {
  ModelSpendProfile,
  SetupWizardState,
  WizardMode,
  WizardStep,
} from './types'

// The setup wizard is a pure API consumer: it persists NOTHING client-side.
// On mount, ``reconcileCompletionFromBackend`` hydrates every substantive piece
// of state (providers, agents, company + applied template, completion flags)
// from the backend, and step/progress is DERIVED from that backend state -- not
// from a persisted client copy that could drift (the data-loss bug class this
// replaced). Transient pre-company flow choices (wizard mode, unsubmitted form
// input) are intentionally ephemeral: they reset on reload and are re-picked,
// which is exactly the "no client persistence" contract.
export const useSetupWizardStore = create<SetupWizardState>()((...a) => ({
  ...createNavigationSlice(...a),
  ...createTemplateSlice(...a),
  ...createCompanySlice(...a),
  ...createAgentsSlice(...a),
  ...createProvidersSlice(...a),
  ...createCompletionSlice(...a),
}))
