import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as companyApi from '@/api/endpoints/company'
import type { CompanyConfig, Department } from '@/api/types'

export const useCompanyStore = defineStore('company', () => {
  const config = ref<CompanyConfig | null>(null)
  const departments = ref<Department[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchConfig() {
    loading.value = true
    error.value = null
    try {
      config.value = await companyApi.getCompanyConfig()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load company config'
    } finally {
      loading.value = false
    }
  }

  async function fetchDepartments() {
    try {
      const result = await companyApi.listDepartments({ limit: 200 })
      departments.value = result.data
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load departments'
    }
  }

  return { config, departments, loading, error, fetchConfig, fetchDepartments }
})
