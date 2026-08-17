/** Living-document, doc-block and doc-version types, plus their value tuples. */

// The ROW is what the endpoint returns: the document plus the last author's
// resolved name. The dashboard has no other living-document shape.
export type { LivingDocumentRow as LivingDocument } from './dtos.gen'
export type {
  BulletListBlock,
  CodeBlock,
  DecisionBlock,
  DocSearchHit,
  DocSummary,
  DocVersion,
  HeadingBlock,
  LinkBlock,
  MetricBlock,
  ProseBlock,
} from './dtos.gen'

export type { DocType } from './enum-values.gen'
export { DOC_TYPE_VALUES } from './enum-values.gen'
