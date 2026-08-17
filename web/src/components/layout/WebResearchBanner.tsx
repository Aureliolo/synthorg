import { useCallback, useState } from 'react'
import { Link } from 'react-router'
import { Globe } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useCapabilities } from '@/hooks/useCapabilities'
import { ROUTES } from '@/router/routes'
import { useSettingsStore } from '@/stores/settings'

// The namespace page reads `?q=` as its filter, so the link lands on the rows
// that clear the fault rather than on the page that happens to hold them.
const WEB_SEARCH_SETTINGS_PATH = `${ROUTES.SETTINGS_NAMESPACE.replace(
  ':namespace',
  'tools',
)}?q=web_search`

const TITLE = 'Web search is not configured'

// Fetching a page needs no credential, so the honest framing is a narrowed
// capability rather than an outage: agents can still read a URL they are given,
// and only finding one is unavailable.
const LOCAL_ONLY_NOTE =
  'Agents can still read pages they are given a URL for. They cannot find one.'

const SIGNUP_NOTE =
  'Pick a provider and bind a connection holding its API key. Every provider needs an account of your own.'

function reuseNote(names: readonly string[]): string {
  if (names.length === 0) return SIGNUP_NOTE
  const listed = names.join(', ')
  // Naming what already exists rather than binding it: a saved connection was
  // authorised for the purpose it was added for, and reaching a second one is
  // the operator's call.
  return `You already have a connection for this provider (${listed}). Bind it to tools.web_search_connection to switch search on.`
}

/**
 * Fail-loud notice that web search is enabled and answering nothing.
 *
 * Renders in the app shell rather than on a page because the blocker is not a
 * property of anywhere in particular: the agents affected by it run whether or
 * not anyone is looking at the Settings page. The verdict is the backend's own
 * (the same one boot builds the provider from), so this cannot report a state
 * the runtime disagrees with.
 *
 * Dismissal writes a backend setting instead of a client flag, both because the
 * dashboard persists no state of its own and because "we are happy with local
 * fetch" is an org-wide decision, not this browser's. It goes through the
 * settings store rather than the endpoint directly, so it inherits the same
 * out-of-order protection, error toasts and shared `entries` every other
 * setting write in the dashboard uses; the store also re-reads the capability
 * matrix, which is what makes the notice disappear.
 */
export function WebResearchBanner() {
  const { capabilities, loading, error } = useCapabilities()
  const updateSetting = useSettingsStore((s) => s.updateSetting)
  const [dismissing, setDismissing] = useState(false)

  const onDismiss = useCallback(() => {
    setDismissing(true)
    // No try/catch: the store owns mutation error UX and reports failure by
    // toast, returning null rather than throwing.
    void updateSetting('tools', 'web_search_notice_dismissed', 'true').finally(
      () => {
        setDismissing(false)
      },
    )
  }, [updateSetting])

  // A failed capability read says nothing about web search, and rendering the
  // blocker from a matrix that never arrived would accuse the operator of a
  // misconfiguration that may not exist.
  if (loading || error !== null || !capabilities.web_search_notify) return null

  return (
    <ErrorBanner
      variant="section"
      severity="warning"
      icon={Globe}
      title={TITLE}
      description={
        <>
          <p>{capabilities.web_search_message}</p>
          <p>{reuseNote(capabilities.web_search_reusable_connections)}</p>
          <p>{LOCAL_ONLY_NOTE}</p>
        </>
      }
      action={
        <Button variant="outline" size="xs" asChild>
          <Link to={WEB_SEARCH_SETTINGS_PATH}>Open web search settings</Link>
        </Button>
      }
      onDismiss={onDismiss}
      dismissDisabled={dismissing}
      className="mx-6 mt-2"
    />
  )
}
