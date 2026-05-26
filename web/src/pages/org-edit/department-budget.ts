export interface BudgetState {
  rounded: number
  isOver: boolean
  isUnder: boolean
  off: boolean
}

export function deriveBudget(budgetTotal: number): BudgetState {
  const isOver = budgetTotal > 100.01
  const isUnder = budgetTotal < 99.99
  return { rounded: Math.round(budgetTotal * 10) / 10, isOver, isUnder, off: isOver || isUnder }
}
