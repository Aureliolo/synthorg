import { ArrowRight, BookOpen, Settings, Users, Wallet, X } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { ROUTES } from '@/router/routes'

interface PostSetupGuidanceCardProps {
  onDismiss: () => void
}

const NEXT_STEPS = [
  {
    icon: Users,
    label: 'Review your org chart',
    to: ROUTES.ORG,
    description: 'See the agent hierarchy you just provisioned.',
  },
  {
    icon: Wallet,
    label: 'Configure budget',
    to: ROUTES.BUDGET,
    description: 'Set monthly limits and alerts before traffic ramps up.',
  },
  {
    icon: Settings,
    label: 'Tune providers',
    to: ROUTES.PROVIDERS,
    description: 'Add additional providers, rotate credentials, audit changes.',
  },
  {
    icon: BookOpen,
    label: 'Read the docs',
    to: ROUTES.DOCUMENTATION,
    description: 'Architecture overview, API reference, deployment notes.',
    external: true as const,
  },
] as const

/**
 * One-time guidance banner shown on the dashboard after setup
 * completes.  The visibility flag lives in localStorage under
 * ``synthorg.firstRun`` so the card stays dismissible across reloads
 * and never reappears once the operator clicks "Got it".
 */
export function PostSetupGuidanceCard({ onDismiss }: PostSetupGuidanceCardProps) {
  return (
    <section
      role="region"
      aria-label="Post-setup guidance"
      className="rounded-md border border-bright bg-card p-card shadow-card-hover"
    >
      <div className="mb-grid-gap flex items-start justify-between gap-grid-gap">
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-semibold text-foreground">
            Welcome. Here are a few next steps
          </h2>
          <p className="text-sm text-text-secondary">
            Setup is complete.  These short paths cover the most common
            first-day actions; you can always come back here from the
            command palette.
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          aria-label="Dismiss post-setup guidance"
        >
          <X aria-hidden="true" className="size-4" />
        </Button>
      </div>

      <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2">
        {NEXT_STEPS.map((step) => {
          const Icon = step.icon
          const external = 'external' in step && step.external
          const linkProps = external
            ? { to: step.to, target: '_blank', rel: 'noreferrer' as const }
            : { to: step.to }
          return (
            <li key={step.label}>
              <Link
                {...linkProps}
                className="flex items-start gap-grid-gap rounded-md border border-border bg-surface p-card transition-colors hover:border-bright hover:bg-card"
              >
                <Icon
                  aria-hidden="true"
                  className="mt-1 size-5 shrink-0 text-accent"
                />
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-1 text-sm font-medium text-foreground">
                    {step.label}
                    <ArrowRight aria-hidden="true" className="size-3" />
                  </div>
                  <p className="text-xs text-text-secondary">{step.description}</p>
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
