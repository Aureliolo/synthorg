import { useEffect, useState } from 'react'

import { listActiveAgents } from '@/api/endpoints/agents'
import { createLogger } from '@/lib/logger'

const log = createLogger('org-roster')

/** Whether the roster can be judged against yet. */
export type OrgRosterStatus = 'loading' | 'ready' | 'failed'

export interface OrgRoster {
  /** The roles the org staffs. Meaningful only once `status` is `ready`. */
  readonly roles: ReadonlySet<string>
  /** Whether the roles above are the answer or a placeholder. */
  readonly status: OrgRosterStatus
}

const EMPTY: ReadonlySet<string> = new Set<string>()

/**
 * The roles the org currently staffs.
 *
 * Plan review needs it to tell an owner that names somebody from one that
 * names nobody: a plan item owned by a role no agent holds cannot be
 * dispatched, and counting it under "all assigned" is what let a plan reach
 * review with most of its items unroutable.
 *
 * Read from `/agents/active`, walking every cursor page. That is the same
 * population the backend validates an edit against, which is the whole point:
 * a single page of the config-time roster would drop roles held only by later
 * agents and flag legitimate items as unroutable, and would admit an inactive
 * agent's role that the API then rejects.
 *
 * `status` is what a caller judges on. While loading, and when the fetch
 * failed, the roles are empty but unknown, and flagging every owner off an
 * answer that never arrived is worse than flagging none.
 */
export function useOrgRoster(): OrgRoster {
  const [roster, setRoster] = useState<OrgRoster>({
    roles: EMPTY,
    status: 'loading',
  })

  useEffect(() => {
    let live = true
    listActiveAgents()
      .then((agents) => {
        if (!live) return
        setRoster({
          roles: new Set(agents.map((agent) => agent.role)),
          status: 'ready',
        })
      })
      .catch((err: unknown) => {
        if (!live) return
        log.warn('Failed to load the org roster', err)
        setRoster({ roles: EMPTY, status: 'failed' })
      })
    return () => {
      live = false
    }
  }, [])

  return roster
}

/**
 * The roles to judge an owner against, or `undefined` while unknown.
 *
 * The plan utilities treat `undefined` as "judge nothing", so a roster that
 * is still loading or that failed to load flags no owner rather than every
 * owner.
 */
export function judgedRoles(roster: OrgRoster): ReadonlySet<string> | undefined {
  return roster.status === 'ready' ? roster.roles : undefined
}
