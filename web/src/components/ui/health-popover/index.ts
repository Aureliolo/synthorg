/**
 * Package boundary for the health-popover sub-package: the component plus its
 * `<ComponentName>Props`, so a consumer can type a wrapper around it.
 *
 * Everything else stays internal. The sub-components (HealthPopoverContent,
 * HealthStatusRow, HealthStatusIcon) are not exported, so their Props types
 * are unreachable surface; the helper types behind them live in
 * `./health-popover.utils`, which siblings import directly because routing
 * them through this barrel would close an import cycle.
 *
 * @public
 */
export { HealthPopover, type HealthPopoverProps } from './HealthPopover'
