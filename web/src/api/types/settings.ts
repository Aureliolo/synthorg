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

/** Frontend-only log level union (inline string union on the wire
 *  via SinkInfo.level; not a named OpenAPI schema). */
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
