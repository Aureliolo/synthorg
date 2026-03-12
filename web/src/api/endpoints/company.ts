import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { CompanyConfig, Department, PaginationParams } from '../types'

export async function getCompanyConfig(): Promise<CompanyConfig> {
  const response = await apiClient.get('/company')
  return unwrap(response)
}

export async function listDepartments(params?: PaginationParams) {
  const response = await apiClient.get('/departments', { params })
  return unwrapPaginated<Department>(response)
}

export async function getDepartment(name: string): Promise<Department> {
  const response = await apiClient.get(`/departments/${name}`)
  return unwrap(response)
}
