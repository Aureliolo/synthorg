import {
  createDepartment as apiCreateDepartment,
  deleteDepartment as apiDeleteDepartment,
  reorderDepartments as apiReorderDepartments,
  updateCompany as apiUpdateCompany,
  updateDepartment as apiUpdateDepartment,
} from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type {
  CreateDepartmentRequest,
  Department,
  UpdateCompanyRequest,
  UpdateDepartmentRequest,
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

async function updateCompanyImpl(
  set: CompanySet,
  get: CompanyGet,
  data: UpdateCompanyRequest,
): Promise<boolean> {
  // Split the two phases so a successful PATCH never gets reported
  // as a failed save just because the follow-up refresh threw: the
  // update has already committed on the server, and treating the
  // refresh error as a mutation failure would leave the form dirty
  // and invite duplicate retries of the same change.
  beginMutation(set)
  try {
    await apiUpdateCompany(data)
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(err, 'Failed to update company', 'Update company failed')
    return false
  }
  // PATCH succeeded. Attempt to refetch the canonical config so the
  // UI reflects the server's post-update view, but do not undo the
  // success signal if the refetch itself fails -- fetchCompanyData
  // already sets its own error state that page-level banners consume.
  try {
    await get().fetchCompanyData()
  } catch (refreshErr) {
    log.warn(
      'Company updated but refresh failed:',
      sanitizeForLog(refreshErr),
    )
  }
  endMutation(set)
  emitSuccessToast('Company updated')
  return true
}

async function createDepartmentImpl(
  set: CompanySet,
  get: CompanyGet,
  data: CreateDepartmentRequest,
): Promise<Department | null> {
  beginMutation(set)
  try {
    const dept = await apiCreateDepartment(data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        departments: [...prev.departments, dept],
      })),
    }))
    emitSuccessToast(`Department ${dept.name} created`)
    return dept
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(
      err,
      'Failed to create department',
      'Create department failed',
    )
    return null
  }
}

async function updateDepartmentImpl(
  set: CompanySet,
  get: CompanyGet,
  name: string,
  data: UpdateDepartmentRequest,
): Promise<Department | null> {
  beginMutation(set)
  try {
    const dept = await apiUpdateDepartment(name, data)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        departments: prev.departments.map((d) =>
          d.name === name ? dept : d,
        ),
      })),
    }))
    emitSuccessToast(`Department ${dept.name} updated`)
    return dept
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(
      err,
      'Failed to update department',
      'Update department failed',
    )
    return null
  }
}

async function deleteDepartmentImpl(
  set: CompanySet,
  get: CompanyGet,
  name: string,
): Promise<boolean> {
  beginMutation(set)
  try {
    await apiDeleteDepartment(name)
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        departments: prev.departments.filter((d) => d.name !== name),
      })),
    }))
    emitSuccessToast(`Department ${name} deleted`)
    return true
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(
      err,
      'Failed to delete department',
      'Delete department failed',
    )
    return false
  }
}

async function reorderDepartmentsImpl(
  set: CompanySet,
  get: CompanyGet,
  orderedNames: string[],
): Promise<boolean> {
  beginMutation(set)
  try {
    const reordered = await apiReorderDepartments({
      department_names: orderedNames,
    })
    set((s) => ({
      savingCount: Math.max(0, s.savingCount - 1),
      ...patchConfig(get, (prev) => ({
        ...prev,
        departments: [...reordered],
      })),
    }))
    emitSuccessToast('Departments reordered')
    return true
  } catch (err) {
    endMutation(set, getErrorMessage(err))
    emitErrorToast(
      err,
      'Failed to reorder departments',
      'Reorder departments failed',
    )
    return false
  }
}

export function createDepartmentActions(set: CompanySet, get: CompanyGet) {
  return {
    updateCompany: (data: UpdateCompanyRequest) =>
      updateCompanyImpl(set, get, data),
    createDepartment: (data: CreateDepartmentRequest) =>
      createDepartmentImpl(set, get, data),
    updateDepartment: (name: string, data: UpdateDepartmentRequest) =>
      updateDepartmentImpl(set, get, name, data),
    deleteDepartment: (name: string) =>
      deleteDepartmentImpl(set, get, name),
    reorderDepartments: (orderedNames: string[]) =>
      reorderDepartmentsImpl(set, get, orderedNames),
  }
}
