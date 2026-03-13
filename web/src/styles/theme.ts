/** Dark theme color tokens used throughout the application. */

export const colors = {
  brand: {
    50: '#eff6ff',
    100: '#dbeafe',
    200: '#bfdbfe',
    300: '#93c5fd',
    400: '#60a5fa',
    500: '#3b82f6',
    600: '#2563eb',
    700: '#1d4ed8',
    800: '#1e40af',
    900: '#1e3a8a',
    950: '#172554',
  },
  surface: {
    0: '#020617',
    50: '#0f172a',
    100: '#1e293b',
    200: '#334155',
    300: '#475569',
    400: '#64748b',
    500: '#94a3b8',
    600: '#cbd5e1',
    700: '#e2e8f0',
    800: '#f1f5f9',
    900: '#f8fafc',
  },
  danger: { 500: '#ef4444', 600: '#dc2626' },
  warning: { 500: '#f59e0b', 600: '#d97706' },
  success: { 500: '#22c55e', 600: '#16a34a' },
} as const

export type Status = 'created' | 'assigned' | 'in_progress' | 'in_review' | 'completed' | 'blocked' | 'failed' | 'interrupted' | 'cancelled' | 'pending' | 'approved' | 'rejected' | 'expired'
export type Priority = 'critical' | 'high' | 'medium' | 'low'
export type RiskLevel = 'critical' | 'high' | 'medium' | 'low'

/** Status color mapping for task/approval badges. */
export const statusColors: Record<Status, string> = {
  created: 'bg-slate-600 text-slate-200',
  assigned: 'bg-blue-600 text-blue-100',
  in_progress: 'bg-amber-600 text-amber-100',
  in_review: 'bg-purple-600 text-purple-100',
  completed: 'bg-green-600 text-green-100',
  blocked: 'bg-red-600 text-red-100',
  failed: 'bg-red-700 text-red-100',
  interrupted: 'bg-orange-600 text-orange-100',
  cancelled: 'bg-gray-600 text-gray-200',
  pending: 'bg-amber-600 text-amber-100',
  approved: 'bg-green-600 text-green-100',
  rejected: 'bg-red-600 text-red-100',
  expired: 'bg-gray-500 text-gray-200',
}

/** Priority color mapping. */
export const priorityColors: Record<Priority, string> = {
  critical: 'bg-red-600 text-red-100',
  high: 'bg-orange-600 text-orange-100',
  medium: 'bg-yellow-600 text-yellow-100',
  low: 'bg-slate-600 text-slate-200',
}

/** Risk level color mapping. */
export const riskColors: Record<RiskLevel, string> = {
  critical: 'bg-red-600 text-red-100',
  high: 'bg-orange-600 text-orange-100',
  medium: 'bg-yellow-600 text-yellow-100',
  low: 'bg-green-600 text-green-100',
}
