import { Skeleton } from '@/components/ui/skeleton'

export function PlansSkeleton() {
  return (
    <div className="space-y-section-gap">
      <div className="flex items-center justify-between">
        <Skeleton className="h-6 w-32" />
      </div>
      <div className="flex flex-col gap-2">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    </div>
  )
}
