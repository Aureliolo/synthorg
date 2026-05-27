export interface BudgetState {
  rounded: number
  isOver: boolean
  isUnder: boolean
  off: boolean
}

export function deriveBudget(budgetTotal: number): BudgetState {
  // Derive over/under from the rounded value so the flags never
  // contradict the one-decimal figure shown in the UI.
  const rounded = Math.round(budgetTotal * 10) / 10
  const isOver = rounded > 100.0
  const isUnder = rounded < 100.0
  return { rounded, isOver, isUnder, off: isOver || isUnder }
}
