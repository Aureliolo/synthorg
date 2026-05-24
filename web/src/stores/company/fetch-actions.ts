import { getCompanyConfig, getDepartmentHealth } from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import type { DepartmentHealth, WsEvent } from '@/api/types'
import { log } from './_helpers'
import type { CompanyGet, CompanySet } from './types'

const ORG_MUTATION_EVENTS: ReadonlySet<string> = new Set([
  'agent.hired', 'agent.fired',
  'company.updated',
  'department.created', 'department.updated', 'department.deleted', 'departments.reordered',
  'agent.created', 'agent.updated', 'agent.deleted', 'agents.reordered',
])

async function fetchCompanyDataImpl(
  set: CompanySet,
  get: CompanyGet,
): Promise<void> {
  const version = get()._refreshVersion + 1
  set({ _refreshVersion: version, loading: true, error: null })
  try {
    const config = await getCompanyConfig()
    if (get()._refreshVersion !== version) return // stale response
    set({ config, loading: false, error: null })
  } catch (err) {
    if (get()._refreshVersion !== version) return // stale error
    set({ loading: false, error: getErrorMessage(err) })
    throw err
  }
}

async function fetchDepartmentHealthsImpl(
  set: CompanySet,
  get: CompanyGet,
): Promise<void> {
  const version = get()._healthRefreshVersion + 1
  set({ _healthRefreshVersion: version })
  try {
    const config = get().config
    if (!config) return
    const healthPromises = config.departments.map((dept) =>
      getDepartmentHealth(dept.name).catch((err: unknown) => {
        log.warn('Health fetch failed for dept:', dept.name, err)
        return null
      }),
    )
    const healthResults = await Promise.all(healthPromises)
    if (get()._healthRefreshVersion !== version) return // stale response
    const departmentHealths = healthResults.filter(
      (h): h is DepartmentHealth => h !== null,
    )
    if (departmentHealths.length === 0 && config.departments.length > 0) {
      set({
        departmentHealths,
        healthError: 'Failed to fetch department health data',
      })
    } else {
      set({ departmentHealths, healthError: null })
    }
  } catch (err) {
    if (get()._healthRefreshVersion !== version) return // stale error
    set({ healthError: getErrorMessage(err) })
  }
}

async function refreshAfterWsEvent(get: CompanyGet): Promise<void> {
  // Sequential: fetchDepartmentHealths needs the freshly fetched
  // config.departments list to know which deps to query, so it
  // must run AFTER fetchCompanyData completes. If fetchCompanyData
  // rejects, run fetchDepartmentHealths against the stale department
  // list rather than skipping it entirely -- a transient config-fetch
  // failure should not block the health refresh, and each fetch
  // sets its own error state so the user still sees what failed.
  const store = get()
  try {
    await store.fetchCompanyData()
  } catch (err) {
    log.warn(
      'WS refresh: fetchCompanyData failed:',
      getErrorMessage(err),
    )
  }
  try {
    await store.fetchDepartmentHealths()
  } catch (err) {
    log.warn(
      'WS refresh: fetchDepartmentHealths failed:',
      getErrorMessage(err),
    )
  }
}

function updateFromWsEventImpl(get: CompanyGet, event: WsEvent): void {
  // Sanitize the WS-supplied event_type before consulting the
  // allowlist so a malformed frame can't smuggle control or bidi
  // characters into the dispatch path.
  const eventType = sanitizeWsString(event.event_type, 64)
  if (eventType === undefined) return
  if (!ORG_MUTATION_EVENTS.has(eventType)) return
  void refreshAfterWsEvent(get)
}

export function createFetchActions(set: CompanySet, get: CompanyGet) {
  return {
    fetchCompanyData: () => fetchCompanyDataImpl(set, get),
    fetchDepartmentHealths: () => fetchDepartmentHealthsImpl(set, get),
    updateFromWsEvent: (event: WsEvent) => updateFromWsEventImpl(get, event),
  }
}
