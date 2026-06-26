import { Checkbox as BaseCheckbox } from '@base-ui/react/checkbox'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface CheckboxProps {
  /** Controlled checked state. */
  checked?: boolean
  /** Uncontrolled initial checked state. */
  defaultChecked?: boolean
  /** Fired with the next checked state when the user toggles. */
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  id?: string
  name?: string
  value?: string
  'aria-label'?: string
  'aria-labelledby'?: string
  className?: string
}

/**
 * Themed checkbox built on the Base UI Checkbox primitive.
 *
 * Renders the box, border, and checked-state accent from design tokens so
 * selection controls match the design system instead of the browser-native
 * ``accent-color`` box that raw ``<input type="checkbox">`` produces.
 */
export function Checkbox({ className, ...props }: CheckboxProps) {
  return (
    <BaseCheckbox.Root
      className={cn(
        'flex size-4 shrink-0 items-center justify-center rounded border border-border bg-card',
        'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        'data-[checked]:border-accent data-[checked]:bg-accent',
        'data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <BaseCheckbox.Indicator className="flex text-background">
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  )
}
