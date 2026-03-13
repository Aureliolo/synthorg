import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as companyApi from '@/api/endpoints/company'
import { getErrorMessage } from '@/utils/errors'
import type { CompanyConfig, Department } from '@/api/types'

export const useCompanyStore = defineStore('company', () => {
  const config = ref<CompanyConfig | null>(null)
  const departments = ref<Department[]>([])
  const loading = ref(false)
  const departmentsLoading = ref(false)
  const error = ref<string | null>(null)

  async function fetchConfig() {
    loading.value = true
    error.value = null
    try {
      config.value = await companyApi.getCompanyConfig()
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchDepartments() {
    departmentsLoading.value = true
    error.value = null
    try {
      const result = await companyApi.listDepartments({ limit: 200 })
      departments.value = result.data
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      departmentsLoading.value = false
    }
  }

  return { config, departments, loading, departmentsLoading, error, fetchConfig, fetchDepartments }
})
