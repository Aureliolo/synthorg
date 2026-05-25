/**
 * Module-scoped state shared across the tasks package's slice files.
 * Owning it here (rather than in any single action file) keeps
 * cross-slice mutators (optimisticTransition / upsertTask /
 * handleWsEvent) coordinated on a single Set instance.
 */
export const pendingTransitions = new Set<string>()
