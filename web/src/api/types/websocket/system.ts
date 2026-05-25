/** System lifecycle, budget, and message WebSocket payload interfaces. */

export interface WsSystemErrorPayload {
  message: string
  code?: string | null
}

export interface WsSystemStartupPayload {
  version?: string | null
}

export interface WsSystemShutdownPayload {
  reason?: string | null
}

export interface WsBudgetRecordAddedPayload {
  amount: number
  currency: string
  category?: string | null
  agent_id?: string | null
}

export interface WsBudgetAlertPayload {
  severity: string
  message: string
  threshold?: number | null
  current?: number | null
  currency: string
}

export interface WsMessagePart {
  type: string
  [key: string]: unknown
}

export interface WsMessageSentPayload {
  message_id: string
  sender: string
  to: string
  content: string
  readonly parts: readonly WsMessagePart[]
}
