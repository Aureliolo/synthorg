import {
  apiClient,
  paginateAll,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  Connection,
  ConnectionTypeMetadata,
  CreateConnectionRequest,
  ForgeAccessibleRepo,
  HealthReport,
  RevealedSecretResponse,
  SecretCaptureRequest,
  SecretCaptureResponse,
  UpdateConnectionRequest,
} from '../types/integrations'

async function listConnectionsPage(
  params: PaginationParams,
): Promise<PaginatedResult<Connection>> {
  const response = await apiClient.get<PaginatedResponse<Connection>>('/connections', {
    params,
  })
  return unwrapPaginated<Connection>(response)
}

export async function listConnections(): Promise<readonly Connection[]> {
  // The backend endpoint is cursor-paginated; walk every page so a
  // workspace with more connections than the default page size is not
  // silently truncated to the first page.
  return paginateAll<Connection>((cursor) =>
    listConnectionsPage({ cursor, limit: 200 }),
  )
}

export async function getConnectionTypes(): Promise<readonly ConnectionTypeMetadata[]> {
  // The backend connection-type/credential-field registry is the single
  // source of truth for form labels, ordering, required + secret flags, and
  // capture mode; the form renders from this rather than a hand-authored map.
  const response =
    await apiClient.get<ApiResponse<readonly ConnectionTypeMetadata[]>>('/connections/types')
  return unwrap(response)
}

export async function captureConnectionSecret(
  draftId: string,
  field: string,
  data: SecretCaptureRequest,
): Promise<SecretCaptureResponse> {
  // Out-of-band capture: the masked value POSTs straight to the write-only
  // endpoint and never enters a connection payload or a chat turn; the reply
  // is an opaque single-use handle the create call references.
  const response = await apiClient.post<ApiResponse<SecretCaptureResponse>>(
    `/connections/drafts/${encodeURIComponent(draftId)}/fields/${encodeURIComponent(field)}/capture`,
    data,
  )
  return unwrap(response)
}

export async function getConnection(name: string): Promise<Connection> {
  const response = await apiClient.get<ApiResponse<Connection>>(
    `/connections/${encodeURIComponent(name)}`,
  )
  return unwrap(response)
}

export async function createConnection(
  data: CreateConnectionRequest,
): Promise<Connection> {
  const response = await apiClient.post<ApiResponse<Connection>>('/connections', data)
  return unwrap(response)
}

export async function updateConnection(
  name: string,
  data: UpdateConnectionRequest,
): Promise<Connection> {
  const response = await apiClient.patch<ApiResponse<Connection>>(
    `/connections/${encodeURIComponent(name)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteConnection(name: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/connections/${encodeURIComponent(name)}`,
  )
  unwrapVoid(response)
}

export async function checkConnectionHealth(name: string): Promise<HealthReport> {
  const response = await apiClient.get<ApiResponse<HealthReport>>(
    `/connections/${encodeURIComponent(name)}/health`,
  )
  return unwrap(response)
}

export async function scanAccessibleRepos(
  name: string,
): Promise<readonly ForgeAccessibleRepo[]> {
  // Discover the repositories the forge connection's token can reach so the
  // operator can select the least-privilege repo scope. Egress is pinned to
  // the connection host backend-side.
  const response = await apiClient.get<ApiResponse<readonly ForgeAccessibleRepo[]>>(
    `/connections/${encodeURIComponent(name)}/accessible-repos`,
  )
  return unwrap(response)
}

export async function revealConnectionSecret(
  name: string,
  field: string,
): Promise<RevealedSecretResponse> {
  const response = await apiClient.get<ApiResponse<RevealedSecretResponse>>(
    `/connections/${encodeURIComponent(name)}/secrets/${encodeURIComponent(field)}`,
  )
  return unwrap(response)
}
