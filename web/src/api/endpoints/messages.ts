import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, Channel, Message, PaginatedResponse, PaginationParams } from '../types'

export async function listMessages(params?: PaginationParams & { channel?: string }): Promise<{ data: Message[]; total: number; offset: number; limit: number }> {
  const response = await apiClient.get<PaginatedResponse<Message>>('/messages', { params })
  return unwrapPaginated<Message>(response)
}

export async function listChannels(): Promise<Channel[]> {
  const response = await apiClient.get<ApiResponse<Channel[]>>('/messages/channels')
  return unwrap(response)
}
