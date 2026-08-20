/**
 * One operator action over a selected set, shared by every list that has one.
 *
 * Its own barrel rather than a copy in each domain's: projects, plans and
 * tasks all delete the same way and a name has one home.
 */

export type { BulkDeleteResult } from './dtos.gen'
