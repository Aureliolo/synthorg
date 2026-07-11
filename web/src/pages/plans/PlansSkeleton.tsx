import { Skeleton } from '@/components/ui/skeleton'

const SKELETON_ROWS = ['a', 'b', 'c', 'd', 'e'] as const

export function PlansSkeleton() {
  return (
    <div className="space-y-section-gap">
      <div className="flex items-center justify-between">
        <Skeleton className="h-6 w-32" />
      </div>
      <div className="flex flex-col gap-2">
        {SKELETON_ROWS.map((row) => (
          <Skeleton key={row} className="h-16 w-full" />
        ))}
      </div>
    </div>
  )
}
