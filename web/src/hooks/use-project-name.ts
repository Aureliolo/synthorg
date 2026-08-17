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
 * @param projectId - Project identifier from the route, or `undefined` before
 *   the route resolves.
 * @returns The project's name, or `UNKNOWN_PROJECT_NAME` while unresolved.
 */
export function useProjectName(projectId: string | undefined): string {
  const [name, setName] = useState<string | null>(null)

  useEffect(() => {
    if (projectId === undefined) return
    let cancelled = false
    const load = async () => {
      try {
        const project = await getProject(projectId)
        if (!cancelled) setName(project.name)
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

  return name ?? UNKNOWN_PROJECT_NAME
}
