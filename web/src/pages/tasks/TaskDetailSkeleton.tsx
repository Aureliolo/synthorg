import { Skeleton } from '@/components/ui/skeleton'

/**
 * Layout-mirroring skeleton for the task detail card. Replaces the bare
 * spinner so the page reserves the loaded card's shape (header, metadata grid,
 * timeline) and content does not jump when the task arrives.
 */
export function TaskDetailSkeleton() {
  return (
    <div className="mx-auto max-w-3xl space-y-section-gap" role="status" aria-label="Loading task">
      {/* Breadcrumb + nav row */}
      <div className="flex items-center gap-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-8 w-24 rounded-md" />
      </div>

      <div className="rounded-lg border border-border bg-card p-card space-y-section-gap">
        {/* Header: title + status */}
        <div className="space-y-2">
          <Skeleton className="h-6 w-2/3" />
          <div className="flex gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-24" />
          </div>
        </div>

        {/* Metadata grid */}
        <div className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="space-y-1.5">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-4 w-24" />
            </div>
          ))}
        </div>

        {/* Timeline */}
        <div className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      </div>
    </div>
  )
}
