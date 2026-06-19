/**
 * Friendly labels for the raw model-tier values surfaced by the setup
 * summaries. The API returns terse size buckets (`large` / `medium` /
 * `small`); operators reading the preview want a capability-oriented label
 * rather than the internal size token.
 */
const MODEL_TIER_LABELS: Record<string, string> = {
  large: 'High capability',
  medium: 'Balanced',
  small: 'Lightweight',
}

/** Map a raw model-tier token to a human-readable label (falls back to the token). */
export function formatModelTier(tier: string | null | undefined): string {
  if (!tier) return 'Unassigned'
  return MODEL_TIER_LABELS[tier] ?? tier
}
