/** Settings registry, sink configuration and parsed company-config entries. */

export type {
  SecurityConfigExportResponse,
  SecurityConfigImportRequest,
  SettingDefinition,
  SettingEntry,
  SinkInfoResponse as SinkInfo,
  SinkRotationResponse as SinkRotation,
  TestSinkConfigResponse as TestSinkResult,
  UpdateSettingRequest,
} from './dtos.gen'

export type {
  SettingLevel,
  SettingNamespace,
  SettingSource,
  SettingType,
} from './enum-values.gen'
export { SETTING_SOURCE_VALUES } from './enum-values.gen'

/** Log level union (inline string on the wire via SinkInfo.level; not a
 *  named OpenAPI schema). Generated from
 *  `synthorg.observability.enums.LogLevel` by
 *  `scripts/generate_backend_enums_ts.py` (drift-gated at pre-push). */
export type { LogLevel } from './backend-enums.gen'
