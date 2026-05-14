/** Authentication and session types. */

export type {
  ChangePasswordRequest,
  CookieSessionResponse as AuthResponse,
  LoginRequest,
  SessionResponse as SessionInfo,
  SetupRequest,
  UserInfoResponse,
  WsTicketResponse,
} from './dtos.gen'

import type { LoginRequest } from './dtos.gen'

/** Shared shape of login + setup credentials (both DTOs are
 *  structurally identical). Kept as a frontend alias for forms that
 *  flow into either endpoint. */
export type CredentialsRequest = LoginRequest

/** @deprecated The backend no longer returns the JWT in the body; the
 *  cookie-based ``AuthResponse`` is the wire shape. Kept for any
 *  legacy call sites that have not migrated. */
export interface TokenResponse {
  token: string
  expires_in: number
  must_change_password: boolean
}
