import { SkeletonCard } from '@/components/ui/skeleton'

export function OrgChartSkeleton() {
  return (
    <div
      className="flex h-full flex-col items-center gap-section-gap pt-12"
      role="status"
      aria-live="polite"
      aria-label="Loading org chart"
    >
      {/* CEO skeleton */}
      <SkeletonCard className="h-20 w-48" />

      {/* Department heads row */}
      <div className="flex flex-wrap justify-center gap-grid-gap">
        <SkeletonCard className="h-16 w-40" />
        <SkeletonCard className="h-16 w-40" />
        <SkeletonCard className="h-16 w-40" />
      </div>

      {/* Team members row */}
      <div className="flex flex-wrap justify-center gap-grid-gap">
        <SkeletonCard className="h-14 w-36" />
        <SkeletonCard className="h-14 w-36" />
        <SkeletonCard className="h-14 w-36" />
        <SkeletonCard className="h-14 w-36" />
      </div>
    </div>
  )
}
