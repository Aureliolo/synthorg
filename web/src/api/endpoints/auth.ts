import { apiClient, unwrap } from '../client'
import type {
  ApiResponse,
  ChangePasswordRequest,
  LoginRequest,
  SetupRequest,
  TokenResponse,
  UserInfoResponse,
} from '../types'

export async function setup(data: SetupRequest): Promise<TokenResponse> {
  const response = await apiClient.post<ApiResponse<TokenResponse>>('/auth/setup', data)
  return unwrap(response)
}

export async function login(data: LoginRequest): Promise<TokenResponse> {
  const response = await apiClient.post<ApiResponse<TokenResponse>>('/auth/login', data)
  return unwrap(response)
}

export async function changePassword(data: ChangePasswordRequest): Promise<UserInfoResponse> {
  const response = await apiClient.post<ApiResponse<UserInfoResponse>>('/auth/change-password', data)
  return unwrap(response)
}

export async function getMe(): Promise<UserInfoResponse> {
  const response = await apiClient.get<ApiResponse<UserInfoResponse>>('/auth/me')
  return unwrap(response)
}
