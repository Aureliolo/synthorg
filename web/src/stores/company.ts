import { create } from 'zustand'
import { createAgentActions } from './company/agent-actions'
import { createDepartmentActions } from './company/department-actions'
import { createFetchActions } from './company/fetch-actions'
import { createOptimisticActions } from './company/optimistic-actions'
import { createTeamActions } from './company/team-actions'
import type { CompanyState } from './company/types'

export type { CompanyState } from './company/types'

export const useCompanyStore = create<CompanyState>()((set, get) => ({
  config: null,
  departmentHealths: [],
  loading: false,
  error: null,
  healthError: null,
  savingCount: 0,
  saveError: null,
  _refreshVersion: 0,
  _healthRefreshVersion: 0,

  ...createFetchActions(set, get),
  ...createDepartmentActions(set, get),
  ...createAgentActions(set, get),
  ...createTeamActions(set, get),
  ...createOptimisticActions(set, get),
}))
