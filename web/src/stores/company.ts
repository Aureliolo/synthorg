import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as companyApi from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import { MAX_PAGE_SIZE } from '@/utils/constants'
import type { CompanyConfig, Department } from '@/api/types'

export const useCompanyStore = defineStore('company', () => {
  const config = ref<CompanyConfig | null>(null)
  const departments = ref<Department[]>([])
  const loading = ref(false)
  const departmentsLoading = ref(false)
  const configError = ref<string | null>(null)
  const departmentsError = ref<string | null>(null)

  let configGen = 0
  let departmentsGen = 0

  async function fetchConfig() {
    const gen = ++configGen
    loading.value = true
    configError.value = null
    try {
      const result = await companyApi.getCompanyConfig()
      if (gen === configGen) {
        config.value = result
      }
    } catch (err) {
      if (gen === configGen) {
        configError.value = getErrorMessage(err)
      }
    } finally {
      if (gen === configGen) {
        loading.value = false
      }
    }
  }

  async function fetchDepartments() {
    const gen = ++departmentsGen
    departmentsLoading.value = true
    departmentsError.value = null
    try {
      let allDepts: Department[] = []
      let offset = 0
      // Paginate until all departments are fetched
      while (true) {
        const result = await companyApi.listDepartments({ limit: MAX_PAGE_SIZE, offset })
        if (gen !== departmentsGen) return // Stale request — abort
        allDepts = [...allDepts, ...result.data]
        if (result.data.length === 0 || allDepts.length >= result.total) break
        offset += MAX_PAGE_SIZE
      }
      if (gen === departmentsGen) {
        departments.value = allDepts
      }
    } catch (err) {
      if (gen === departmentsGen) {
        departmentsError.value = getErrorMessage(err)
      }
    } finally {
      if (gen === departmentsGen) {
        departmentsLoading.value = false
      }
    }
  }

  return {
    config,
    departments,
    loading,
    departmentsLoading,
    configError,
    departmentsError,
    fetchConfig,
    fetchDepartments,
  }
})
