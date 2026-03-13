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

  async function fetchConfig() {
    loading.value = true
    configError.value = null
    try {
      config.value = await companyApi.getCompanyConfig()
    } catch (err) {
      configError.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchDepartments() {
    departmentsLoading.value = true
    departmentsError.value = null
    try {
      const result = await companyApi.listDepartments({ limit: MAX_PAGE_SIZE })
      departments.value = result.data
    } catch (err) {
      departmentsError.value = getErrorMessage(err)
    } finally {
      departmentsLoading.value = false
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
