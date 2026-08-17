import { useCallback, useState } from 'react'
import { Link } from 'react-router'
import { Globe } from 'lucide-react'

import { updateSetting } from '@/api/endpoints/settings'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { refreshCapabilities, useCapabilities } from '@/hooks/useCapabilities'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { useToastStore } from '@/stores/toast'

const log = createLogger('WebResearchBanner')

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
 * fetch" is an org-wide decision, not this browser's.
 */
export function WebResearchBanner() {
  const { capabilities, loading, error } = useCapabilities()
  const toast = useToastStore((s) => s.add)
  const [dismissing, setDismissing] = useState(false)

  const onDismiss = useCallback(() => {
    setDismissing(true)
    void updateSetting('tools', 'web_search_notice_dismissed', { value: 'true' })
      .then(refreshCapabilities)
      .catch((err: unknown) => {
        log.error('web_search_notice_dismiss_failed', err)
        toast({
          variant: 'error',
          title: 'Could not dismiss the notice',
          description: 'The setting did not save. Try again in a moment.',
        })
      })
      .finally(() => {
        setDismissing(false)
      })
  }, [toast])

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
          <span className="block">{capabilities.web_search_message}</span>
          <span className="block">
            {reuseNote(capabilities.web_search_reusable_connections)}
          </span>
          <span className="block">{LOCAL_ONLY_NOTE}</span>
        </>
      }
      action={
        <Button variant="outline" size="xs" asChild>
          <Link to={WEB_SEARCH_SETTINGS_PATH}>Open web search settings</Link>
        </Button>
      }
      onDismiss={dismissing ? undefined : onDismiss}
      className="mx-6 mt-2"
    />
  )
}
