import { useEffect, useState } from 'react'

import { getProject } from '@/api/endpoints/projects'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('use-project-name')

/**
 * What a breadcrumb shows where a project's name belongs and none resolved.
 *
 * A project reached by a deep link whose row is gone has no name to give, and
 * its key names nothing an operator could act on, so the crumb says so in its
 * own words rather than printing the identifier from the URL.
 */
const UNKNOWN_PROJECT_NAME = 'Unknown project'

/**
 * Resolve a project's display name from the id in the route.
 *
 * A page that only ever holds the route parameter has nothing but the key, and
 * a crumb reading `a3f7b2c1-...` tells an operator nothing. One read answers
 * it; until it lands, and if it fails, the caller's own words stand in.
 *
 * This reads the project the page is ABOUT, which is not the client-side
 * reference lookup the Names, Never Ids rule refuses. That rule is about a row
 * carrying somebody else's key: resolving one in the browser paints the key on
 * every cold load, so the backend answers it beside the row. Here the project
 * is the subject of the route, its name is its own, and nothing but its own
 * read can supply it. The key still never renders: `UNKNOWN_PROJECT_NAME`
 * stands in, never the identifier from the URL.
 *
 * The name is stored with the id it was read for and returned only while the
 * two agree, because held alone it outlives its own route: moving between two
 * projects left the previous name over the new one's page until the next read
 * landed, and permanently if that read failed.
 *
 * @param projectId - Project identifier from the route, or `undefined` before
 *   the route resolves.
 * @returns The project's name, or `UNKNOWN_PROJECT_NAME` while unresolved.
 */
export function useProjectName(projectId: string | undefined): string {
  const [resolved, setResolved] = useState<{ id: string; name: string } | null>(null)

  useEffect(() => {
    if (projectId === undefined) return
    let cancelled = false
    const load = async () => {
      try {
        const project = await getProject(projectId)
        if (!cancelled) setResolved({ id: projectId, name: project.name })
      } catch (err) {
        // A missing name is a first-class outcome here, not a page failure:
        // the crumb has its own words for it and the page's own content
        // reports its own errors.
        log.warn('get_project_failed', getErrorMessage(err))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [projectId])

  return resolved !== null && resolved.id === projectId
    ? resolved.name
    : UNKNOWN_PROJECT_NAME
}
