import { Skeleton, SkeletonText } from '@/components/ui/skeleton'

function MessageRowSkeleton() {
  return (
    <div className="flex gap-3 rounded-lg border border-border bg-card p-card">
      <Skeleton className="size-6 shrink-0 rounded-full" />
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-16" />
        </div>
        <SkeletonText lines={2} />
      </div>
    </div>
  )
}

export function MessagesSkeleton() {
  return (
    <div className="flex gap-section-gap" role="status" aria-label="Loading messages">
      {/* Channel sidebar skeleton */}
      <div className="flex w-56 shrink-0 flex-col gap-2 border-r border-border pr-4">
        <Skeleton className="mb-2 h-4 w-20" />
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full rounded-md" />
        ))}
      </div>

      {/* Message list skeleton */}
      <div className="flex flex-1 flex-col gap-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-8 w-full rounded-md" />
        {Array.from({ length: 4 }, (_, i) => (
          <MessageRowSkeleton key={i} />
        ))}
      </div>
    </div>
  )
}
