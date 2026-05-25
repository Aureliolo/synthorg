/** Shared structural types for the version-rollback timeline package. */

/** Minimum shape consumed by ``VersionTimeline`` / ``VersionHistoryItem``. */
export interface TimelineItem {
  readonly id: string
  readonly version: number
  readonly created_at: string
}
