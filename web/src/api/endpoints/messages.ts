import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { Channel, Message, PaginationParams } from '../types'

export async function listMessages(params?: PaginationParams & { channel?: string }) {
  const response = await apiClient.get('/messages', { params })
  return unwrapPaginated<Message>(response)
}

export async function listChannels(): Promise<Channel[]> {
  const response = await apiClient.get('/messages/channels')
  const data = unwrap<Channel[]>(response)
  return data
}
