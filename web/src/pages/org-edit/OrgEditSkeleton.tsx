import { Skeleton, SkeletonCard } from '@/components/ui/skeleton'

export function OrgEditSkeleton() {
  return (
    <div className="space-y-section-gap" role="status" aria-live="polite" aria-label="Loading organisation editor">
      {/* Title skeleton */}
      <Skeleton className="h-8 w-56" />
      {/* Tab bar skeleton */}
      <div className="flex gap-4">
        <Skeleton className="h-9 w-24 rounded-md" />
        <Skeleton className="h-9 w-24 rounded-md" />
        <Skeleton className="h-9 w-32 rounded-md" />
      </div>
      {/* Content area skeleton */}
      <div
        className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1"
        data-testid="skeleton-content"
      >
        <SkeletonCard header lines={4} />
        <SkeletonCard header lines={4} />
        <SkeletonCard header lines={3} />
      </div>
    </div>
  )
}
