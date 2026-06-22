import { Skeleton, SkeletonCard } from '@/components/ui/skeleton'

function ColumnSkeleton({ cards }: { cards: number }) {
  return (
    <div className="flex w-72 shrink-0 flex-col gap-3">
      <div className="flex items-center justify-between px-1">
        <Skeleton className="h-4 w-24 rounded" />
        <Skeleton className="size-5 rounded-full" />
      </div>
      <div className="space-y-3">
        {Array.from({ length: cards }, (_, i) => (
          <SkeletonCard key={i} header lines={2} />
        ))}
      </div>
    </div>
  )
}

export function TaskBoardSkeleton() {
  return (
    <div className="space-y-section-gap" role="status" aria-label="Loading task board">
      {/* Filter bar skeleton */}
      <div className="flex items-center gap-3">
        <Skeleton className="h-8 w-32 rounded-md" />
        <Skeleton className="h-8 w-28 rounded-md" />
        <Skeleton className="h-8 w-28 rounded-md" />
        <Skeleton className="h-8 flex-1 max-w-xs rounded-md" />
        <Skeleton className="ml-auto h-8 w-24 rounded-md" />
      </div>
      {/* Columns skeleton: scroll (not clip) so the tablet layout matches the
          real board's overflow-x-auto behaviour. */}
      <div className="flex gap-4 overflow-x-auto">
        <ColumnSkeleton cards={3} />
        <ColumnSkeleton cards={2} />
        <ColumnSkeleton cards={3} />
        <ColumnSkeleton cards={1} />
        <ColumnSkeleton cards={2} />
        <ColumnSkeleton cards={1} />
      </div>
    </div>
  )
}
