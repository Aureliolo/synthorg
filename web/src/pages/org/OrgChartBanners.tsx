import { ErrorBanner } from '@/components/ui/error-banner'

export interface OrgChartBannersProps {
  error: string | null
  commError: string | null
  commTruncated: boolean
  wsConnected: boolean
  wsSetupError: string | null
}

/** Stacked status banners above the org chart canvas. */
export function OrgChartBanners({
  error,
  commError,
  commTruncated,
  wsConnected,
  wsSetupError,
}: OrgChartBannersProps) {
  return (
    <>
      {Boolean(error) && (
        <ErrorBanner severity="error" title="Could not load org chart" description={error} />
      )}
      {Boolean(commError) && (
        <ErrorBanner
          variant="inline"
          severity="warning"
          title="Communication data unavailable"
          description={commError}
        />
      )}
      {commTruncated && !commError && (
        <ErrorBanner
          variant="inline"
          severity="info"
          title="Partial communication graph"
          description="Message limit reached; showing available data."
        />
      )}
      {Boolean(!wsConnected && wsSetupError) && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates unavailable"
          description={wsSetupError}
        />
      )}
    </>
  )
}
