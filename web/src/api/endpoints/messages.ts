import { apiClient, unwrapPaginated, withSignal, type PaginatedResult } from '../client'
import type { PaginatedResponse, PaginationParams } from '../types/http'
import type { Channel, Message } from '../types/messages'

export async function listMessages(params?: PaginationParams & { channel?: string; signal?: AbortSignal }): Promise<PaginatedResult<Message>> {
  const { signal, ...queryParams } = params ?? {}
  const response = await apiClient.get<PaginatedResponse<Message>>('/messages', withSignal(signal, { params: queryParams }))
  return unwrapPaginated<Message>(response)
}

export async function listChannels(
  params?: PaginationParams & { signal?: AbortSignal },
): Promise<PaginatedResult<Channel>> {
  const { signal, ...queryParams } = params ?? {}
  const response = await apiClient.get<PaginatedResponse<Channel>>(
    '/messages/channels',
    withSignal(signal, { params: queryParams }),
  )
  return unwrapPaginated<Channel>(response)
}
