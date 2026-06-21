import { Skeleton, SkeletonCard, SkeletonText } from '@/components/ui/skeleton'

export function WizardSkeleton() {
  return (
    // Mirror WizardShell's outer chrome (h-dvh / max-w-4xl / px-4 / py-8) so
    // resolving from skeleton to the live shell does not jump the content.
    <div className="flex h-dvh flex-col items-center bg-background">
      <div className="w-full max-w-4xl space-y-section-gap px-4 py-8">
        {/* Progress bar skeleton */}
        <div className="flex items-center justify-center gap-grid-gap">
          {Array.from({ length: 7 }, (_, i) => (
            <div key={i} className="flex flex-col items-center gap-1">
              <Skeleton className="size-8 rounded-full" />
              <Skeleton className="h-3 w-12" />
            </div>
          ))}
        </div>

        {/* Content skeleton */}
        <div className="space-y-section-gap">
          <Skeleton className="h-6 w-48" />
          <SkeletonText lines={2} />
          <SkeletonCard />
          <SkeletonCard />
        </div>

        {/* Navigation skeleton */}
        <div className="flex justify-between border-t border-border pt-4">
          <Skeleton className="h-9 w-20" />
          <Skeleton className="h-9 w-20" />
        </div>
      </div>
    </div>
  )
}
