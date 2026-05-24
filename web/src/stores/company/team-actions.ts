import {
  createTeam as apiCreateTeam,
  deleteTeam as apiDeleteTeam,
  reorderTeams as apiReorderTeams,
  updateTeam as apiUpdateTeam,
} from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type {
  CompanyConfig,
  CreateTeamRequest,
  Department,
  TeamConfig,
  UpdateTeamRequest,
} from '@/api/types/org'
import {
  beginMutation,
  emitErrorToast,
  emitSuccessToast,
  endMutation,
  log,
  patchConfig,
} from './_helpers'
import type { CompanyGet, CompanySet } from './types'

function withDepartmentPatch(
  deptName: string,
  transform: (d: Department) => Department,
): (prev: CompanyConfig) => CompanyConfig {
  return (prev) => ({
    ...prev,
    departments: prev.departments.map((d) =>
      d.name === deptName ? transform(d) : d,
    ),
  })
}

async function createTeamImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  data: CreateTeamRequest,
): Promise<TeamConfig | null> {
  beginMutation(set)
  try {
    const team = await apiCreateTeam(deptName, data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(
        get,
        withDepartmentPatch(deptName, (d) => ({
          ...d,
          teams: [...d.teams, team],
        })),
      ),
    }))
    emitSuccessToast(`Team ${team.name} created`)
    return team
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to create team', 'Create team failed')
    return null
  }
}

async function updateTeamImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  teamName: string,
  data: UpdateTeamRequest,
): Promise<TeamConfig | null> {
  beginMutation(set)
  try {
    const team = await apiUpdateTeam(deptName, teamName, data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(
        get,
        withDepartmentPatch(deptName, (d) => ({
          ...d,
          teams: d.teams.map((t) => (t.name === teamName ? team : t)),
        })),
      ),
    }))
    emitSuccessToast(`Team ${team.name} updated`)
    return team
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to update team', 'Update team failed')
    return null
  }
}

async function deleteTeamImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  teamName: string,
  reassignTo: string | undefined,
): Promise<boolean> {
  beginMutation(set)
  try {
    await apiDeleteTeam(deptName, teamName, reassignTo)
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to delete team', 'Delete team failed')
    return false
  }
  // Delete succeeded on the server. A failure in the follow-up
  // refetch is a separate concern: do NOT report the delete as
  // failed (that would invite duplicate retries of an already
  // applied mutation). Surface refetch failures via the
  // fetchCompanyData error state and a warning log.
  if (reassignTo) {
    try {
      // Reassign rebalances agents across teams; refetch to pick
      // up the canonical post-reassign config rather than try to
      // re-derive it locally.
      await get().fetchCompanyData()
    } catch (refreshErr) {
      log.warn(
        'Team deleted but refresh failed:',
        sanitizeForLog(refreshErr),
      )
    }
    endMutation(set)
  } else {
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(
        get,
        withDepartmentPatch(deptName, (d) => ({
          ...d,
          teams: d.teams.filter((t) => t.name !== teamName),
        })),
      ),
    }))
  }
  emitSuccessToast(`Team ${teamName} deleted`)
  return true
}

async function reorderTeamsImpl(
  set: CompanySet,
  get: CompanyGet,
  deptName: string,
  orderedNames: string[],
): Promise<boolean> {
  beginMutation(set)
  try {
    const reordered = await apiReorderTeams(deptName, {
      team_names: orderedNames,
    })
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(
        get,
        withDepartmentPatch(deptName, (d) => ({ ...d, teams: reordered })),
      ),
    }))
    emitSuccessToast('Teams reordered')
    return true
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to reorder teams', 'Reorder teams failed')
    return false
  }
}

export function createTeamActions(set: CompanySet, get: CompanyGet) {
  return {
    createTeam: (deptName: string, data: CreateTeamRequest) =>
      createTeamImpl(set, get, deptName, data),
    updateTeam: (deptName: string, teamName: string, data: UpdateTeamRequest) =>
      updateTeamImpl(set, get, deptName, teamName, data),
    deleteTeam: (deptName: string, teamName: string, reassignTo?: string) =>
      deleteTeamImpl(set, get, deptName, teamName, reassignTo),
    reorderTeams: (deptName: string, orderedNames: string[]) =>
      reorderTeamsImpl(set, get, deptName, orderedNames),
  }
}
