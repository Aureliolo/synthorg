import type { PostureName } from '@/api/types'

/** Visual + descriptive metadata for a named operating posture. */
export interface PostureInfo {
  /** Short human label. */
  label: string
  /** One-sentence summary of what the posture configures. */
  description: string
  /** Feature-flag bundle the posture activates (display order). */
  featureFlags: readonly string[]
  /** StatPill colour intent. */
  tone: 'accent' | 'success' | 'warning' | 'danger' | 'muted'
}

/**
 * Static table mapping each posture to its operator-facing metadata.
 * Mirrors the ``PostureName`` docstring in
 * ``src/synthorg/templates/postures.py``; kept in lockstep by the
 * ``posture-info`` unit test, which asserts every enum member has an
 * entry.
 */
export const POSTURE_INFO = {
  autonomous: {
    label: 'Autonomous',
    description:
      'High-autonomy delivery: steering and the knowledge substrate are on; human chat modes are off.',
    featureFlags: ['Knowledge substrate', 'Mid-flight steering'],
    tone: 'accent',
  },
  supervised_client_facing: {
    label: 'Supervised, client-facing',
    description:
      'Human-in-the-loop client work: group chat and agent invite are on for stakeholder collaboration.',
    featureFlags: ['Group chat', 'Agent invite'],
    tone: 'success',
  },
  knowledge_heavy: {
    label: 'Knowledge-heavy',
    description:
      'Knowledge-substrate-grounded work: entailment grounding and a shared knowledge base.',
    featureFlags: ['Knowledge substrate', 'Entailment grounding'],
    tone: 'accent',
  },
  cost_disciplined: {
    label: 'Cost-disciplined',
    description:
      'Budget-first operation: auto-downgrade is on and optional features are off to minimise spend.',
    featureFlags: ['Budget auto-downgrade'],
    tone: 'warning',
  },
  security_hardened: {
    label: 'Security-hardened',
    description:
      'Security-first operation: the red-team completion gate is on at a lowered stakes floor; self-extension is off.',
    featureFlags: ['Red-team gate', 'Self-extension off'],
    tone: 'danger',
  },
  research_autonomous: {
    label: 'Research-autonomous',
    description:
      'Autonomous inquiry: knowledge substrate, steering, and clarify-or-park plus routing proposals are on.',
    featureFlags: ['Knowledge substrate', 'Mid-flight steering', 'Clarify-or-park'],
    tone: 'accent',
  },
} as const satisfies Record<PostureName, PostureInfo>
