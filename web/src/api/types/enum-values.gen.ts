// AUTO-GENERATED: do not edit by hand.
// Regenerate with: uv run python scripts/generate_dto_types_ts.py
// Drift check (pre-push): uv run python scripts/check_dto_types_ts_in_sync.py
// Source: src/synthorg/api/**/*.py (via scripts/export_openapi.py + openapi-typescript)
// Contract: web/CLAUDE.md -> 'Generated DTO types (MANDATORY)'

export const ACTIVITY_EVENT_TYPE_VALUES = [
    'hired',
    'onboarded',
    'fired',
    'offboarded',
    'status_changed',
    'promoted',
    'demoted',
    'task_started',
    'task_completed',
    'cost_incurred',
    'tool_used',
    'delegation_sent',
    'delegation_received',
] as const
export type ActivityEventType = (typeof ACTIVITY_EVENT_TYPE_VALUES)[number]

export const AGENT_STATUS_VALUES = [
    'active',
    'onboarding',
    'on_leave',
    'terminated',
] as const
export type AgentStatus = (typeof AGENT_STATUS_VALUES)[number]

export const APPROVAL_RISK_LEVEL_VALUES = [
    'low',
    'medium',
    'high',
    'critical',
] as const
export type ApprovalRiskLevel = (typeof APPROVAL_RISK_LEVEL_VALUES)[number]

export const APPROVAL_SOURCE_VALUES = [
    'parked_context',
    'review_gate',
] as const
export type ApprovalSource = (typeof APPROVAL_SOURCE_VALUES)[number]

export const APPROVAL_STATUS_VALUES = [
    'pending',
    'approved',
    'rejected',
    'expired',
] as const
export type ApprovalStatus = (typeof APPROVAL_STATUS_VALUES)[number]

export const ARTIFACT_TYPE_VALUES = [
    'code',
    'tests',
    'documentation',
] as const
export type ArtifactType = (typeof ARTIFACT_TYPE_VALUES)[number]

export const AUTH_METHOD_VALUES = [
    'api_key',
    'oauth2',
    'basic_auth',
    'bearer_token',
    'custom',
] as const
export type AuthMethod = (typeof AUTH_METHOD_VALUES)[number]

export const AUTH_TYPE_VALUES = [
    'api_key',
    'oauth',
    'custom_header',
    'subscription',
    'none',
] as const
export type AuthType = (typeof AUTH_TYPE_VALUES)[number]

export const AUTONOMY_LEVEL_VALUES = [
    'full',
    'semi',
    'supervised',
    'locked',
] as const
export type AutonomyLevel = (typeof AUTONOMY_LEVEL_VALUES)[number]

export const AUTONOMY_STRATEGY_TYPE_VALUES = [
    'human_only',
    'performance_gated',
    'budget_aware',
    'escalation_chain',
] as const
export type AutonomyStrategyType = (typeof AUTONOMY_STRATEGY_TYPE_VALUES)[number]

export const BACKUP_COMPONENT_VALUES = [
    'persistence',
    'memory',
    'config',
] as const
export type BackupComponent = (typeof BACKUP_COMPONENT_VALUES)[number]

export const BACKUP_TRIGGER_VALUES = [
    'scheduled',
    'manual',
    'shutdown',
    'startup',
    'pre_migration',
] as const
export type BackupTrigger = (typeof BACKUP_TRIGGER_VALUES)[number]

export const BUCKET_SIZE_VALUES = [
    'hour',
    'day',
] as const
export type BucketSize = (typeof BUCKET_SIZE_VALUES)[number]

export const CEREMONY_STRATEGY_TYPE_VALUES = [
    'task_driven',
    'calendar',
    'hybrid',
    'event_driven',
    'budget_driven',
    'throughput_adaptive',
    'external_trigger',
    'milestone_driven',
] as const
export type CeremonyStrategyType = (typeof CEREMONY_STRATEGY_TYPE_VALUES)[number]

export const CHANNEL_TYPE_VALUES = [
    'topic',
    'direct',
    'broadcast',
] as const
export type ChannelType = (typeof CHANNEL_TYPE_VALUES)[number]

export const CODE_EXECUTION_ISOLATION_VALUES = [
    'containerized',
    'process',
] as const
export type CodeExecutionIsolation = (typeof CODE_EXECUTION_ISOLATION_VALUES)[number]

export const COLLABORATION_PREFERENCE_VALUES = [
    'independent',
    'pair',
    'team',
] as const
export type CollaborationPreference = (typeof COLLABORATION_PREFERENCE_VALUES)[number]

export const COMMUNICATION_VERBOSITY_VALUES = [
    'terse',
    'balanced',
    'verbose',
] as const
export type CommunicationVerbosity = (typeof COMMUNICATION_VERBOSITY_VALUES)[number]

export const COMPANY_TYPE_VALUES = [
    'solo_founder',
    'startup',
    'dev_shop',
    'product_team',
    'agency',
    'full_company',
    'research_lab',
    'consultancy',
    'data_team',
    'custom',
] as const
export type CompanyType = (typeof COMPANY_TYPE_VALUES)[number]

export const COMPARATOR_VALUES = [
    'lt',
    'le',
    'gt',
    'ge',
    'eq',
    'ne',
] as const
export type Comparator = (typeof COMPARATOR_VALUES)[number]

export const COMPLEXITY_VALUES = [
    'simple',
    'medium',
    'complex',
    'epic',
] as const
export type Complexity = (typeof COMPLEXITY_VALUES)[number]

export const CONFLICT_APPROACH_VALUES = [
    'avoid',
    'accommodate',
    'compete',
    'compromise',
    'collaborate',
] as const
export type ConflictApproach = (typeof CONFLICT_APPROACH_VALUES)[number]

export const CONFLICT_TYPE_VALUES = [
    'architecture',
    'implementation',
    'priority',
    'resource',
    'process',
    'other',
] as const
export type ConflictType = (typeof CONFLICT_TYPE_VALUES)[number]

export const CONNECTION_STATUS_VALUES = [
    'healthy',
    'degraded',
    'unhealthy',
    'unknown',
] as const
export type ConnectionStatus = (typeof CONNECTION_STATUS_VALUES)[number]

export const CONNECTION_TYPE_VALUES = [
    'github',
    'gitlab',
    'gitea',
    'forgejo',
    'slack',
    'smtp',
    'database',
    'generic_http',
    'oauth_app',
    'a2a_peer',
] as const
export type ConnectionType = (typeof CONNECTION_TYPE_VALUES)[number]

export const CONTENT_TYPE_VALUES = [
    'procedural',
    'semantic',
    'tool_patterns',
] as const
export type ContentType = (typeof CONTENT_TYPE_VALUES)[number]

export const COORDINATION_TOPOLOGY_VALUES = [
    'sas',
    'centralized',
    'decentralized',
    'context_dependent',
    'auto',
] as const
export type CoordinationTopology = (typeof COORDINATION_TOPOLOGY_VALUES)[number]

export const CREATIVITY_LEVEL_VALUES = [
    'low',
    'medium',
    'high',
] as const
export type CreativityLevel = (typeof CREATIVITY_LEVEL_VALUES)[number]

export const DECISION_MAKING_STYLE_VALUES = [
    'analytical',
    'intuitive',
    'consultative',
    'directive',
] as const
export type DecisionMakingStyle = (typeof DECISION_MAKING_STYLE_VALUES)[number]

export const DEPARTMENT_NAME_VALUES = [
    'executive',
    'product',
    'design',
    'engineering',
    'quality_assurance',
    'data_analytics',
    'operations',
    'creative_marketing',
    'security',
] as const
export type DepartmentName = (typeof DEPARTMENT_NAME_VALUES)[number]

export const DRIFT_ACTION_VALUES = [
    'no_action',
    'notify',
    'retrain',
    'escalate',
] as const
export type DriftAction = (typeof DRIFT_ACTION_VALUES)[number]

export const ENTITY_SOURCE_VALUES = [
    'auto',
    'config',
    'api',
] as const
export type EntitySource = (typeof ENTITY_SOURCE_VALUES)[number]

export const ENTITY_TIER_VALUES = [
    'core',
    'user',
] as const
export type EntityTier = (typeof ENTITY_TIER_VALUES)[number]

export const ERROR_CATEGORY_VALUES = [
    'auth',
    'validation',
    'not_found',
    'conflict',
    'rate_limit',
    'budget_exhausted',
    'provider_error',
    'internal',
] as const
export type ErrorCategory = (typeof ERROR_CATEGORY_VALUES)[number]

export const ESCALATION_STATUS_VALUES = [
    'pending',
    'decided',
    'expired',
    'cancelled',
] as const
export type EscalationStatus = (typeof ESCALATION_STATUS_VALUES)[number]

export const EVALUATION_CONFIDENCE_VALUES = [
    'high',
    'low',
] as const
export type EvaluationConfidence = (typeof EVALUATION_CONFIDENCE_VALUES)[number]

export const FILE_SYSTEM_SCOPE_VALUES = [
    'workspace_only',
    'project_directory',
    'full',
] as const
export type FileSystemScope = (typeof FILE_SYSTEM_SCOPE_VALUES)[number]

export const FINE_TUNE_STAGE_VALUES = [
    'idle',
    'generating_data',
    'mining_negatives',
    'training',
    'evaluating',
    'deploying',
    'complete',
    'failed',
] as const
export type FineTuneStage = (typeof FINE_TUNE_STAGE_VALUES)[number]

export const FINISH_REASON_VALUES = [
    'stop',
    'max_tokens',
    'tool_use',
    'content_filter',
    'error',
] as const
export type FinishReason = (typeof FINISH_REASON_VALUES)[number]

export const GIT_ACCESS_VALUES = [
    'local_only',
    'read_and_branch',
    'full',
] as const
export type GitAccess = (typeof GIT_ACCESS_VALUES)[number]

export const HUMAN_ROLE_VALUES = [
    'ceo',
    'manager',
    'board_member',
    'pair_programmer',
    'observer',
    'system',
] as const
export type HumanRole = (typeof HUMAN_ROLE_VALUES)[number]

export const INTERRUPT_TYPE_VALUES = [
    'tool_approval',
    'info_request',
] as const
export type InterruptType = (typeof INTERRUPT_TYPE_VALUES)[number]

export const LLM_CALL_CATEGORY_VALUES = [
    'productive',
    'coordination',
    'system',
    'embedding',
] as const
export type LLMCallCategory = (typeof LLM_CALL_CATEGORY_VALUES)[number]

export const LIFECYCLE_EVENT_TYPE_VALUES = [
    'hired',
    'onboarded',
    'fired',
    'offboarded',
    'status_changed',
    'promoted',
    'demoted',
] as const
export type LifecycleEventType = (typeof LIFECYCLE_EVENT_TYPE_VALUES)[number]

export const MEETING_PHASE_VALUES = [
    'agenda_broadcast',
    'round_robin_turn',
    'position_paper',
    'input_gathering',
    'discussion',
    'synthesis',
    'summary',
    'premortem',
    'devil_advocate',
] as const
export type MeetingPhase = (typeof MEETING_PHASE_VALUES)[number]

export const MEETING_PROTOCOL_TYPE_VALUES = [
    'round_robin',
    'position_papers',
    'structured_phases',
] as const
export type MeetingProtocolType = (typeof MEETING_PROTOCOL_TYPE_VALUES)[number]

export const MEETING_STATUS_VALUES = [
    'scheduled',
    'in_progress',
    'completed',
    'failed',
    'cancelled',
    'budget_exhausted',
] as const
export type MeetingStatus = (typeof MEETING_STATUS_VALUES)[number]

export const MEMORY_CATEGORY_VALUES = [
    'working',
    'episodic',
    'semantic',
    'procedural',
    'social',
] as const
export type MemoryCategory = (typeof MEMORY_CATEGORY_VALUES)[number]

export const MEMORY_LEVEL_VALUES = [
    'persistent',
    'project',
    'session',
    'none',
] as const
export type MemoryLevel = (typeof MEMORY_LEVEL_VALUES)[number]

export const MESSAGE_PRIORITY_VALUES = [
    'low',
    'normal',
    'high',
    'urgent',
] as const
export type MessagePriority = (typeof MESSAGE_PRIORITY_VALUES)[number]

export const MESSAGE_TYPE_VALUES = [
    'task_update',
    'question',
    'announcement',
    'review_request',
    'approval',
    'delegation',
    'status_report',
    'escalation',
    'meeting_contribution',
    'hr_notification',
    'dissent',
    'context_injection',
] as const
export type MessageType = (typeof MESSAGE_TYPE_VALUES)[number]

export const NETWORK_MODE_VALUES = [
    'none',
    'allowlist_only',
    'open',
] as const
export type NetworkMode = (typeof NETWORK_MODE_VALUES)[number]

export const ORG_ROLE_VALUES = [
    'owner',
    'department_admin',
    'editor',
    'viewer',
] as const
export type OrgRole = (typeof ORG_ROLE_VALUES)[number]

export const POLICY_FIELD_ORIGIN_VALUES = [
    'project',
    'department',
    'default',
] as const
export type PolicyFieldOrigin = (typeof POLICY_FIELD_ORIGIN_VALUES)[number]

export const PRESET_SOURCE_VALUES = [
    'builtin',
    'custom',
] as const
export type PresetSource = (typeof PRESET_SOURCE_VALUES)[number]

export const PRIORITY_VALUES = [
    'critical',
    'high',
    'medium',
    'low',
] as const
export type Priority = (typeof PRIORITY_VALUES)[number]

export const PROJECT_STATUS_VALUES = [
    'planning',
    'active',
    'on_hold',
    'completed',
    'cancelled',
] as const
export type ProjectStatus = (typeof PROJECT_STATUS_VALUES)[number]

export const PROPOSAL_ALTITUDE_VALUES = [
    'config_tuning',
    'architecture',
    'prompt_tuning',
    'code_modification',
] as const
export type ProposalAltitude = (typeof PROPOSAL_ALTITUDE_VALUES)[number]

export const PROVIDER_HEALTH_STATUS_VALUES = [
    'up',
    'degraded',
    'down',
    'unknown',
] as const
export type ProviderHealthStatus = (typeof PROVIDER_HEALTH_STATUS_VALUES)[number]

export const READINESS_OUTCOME_VALUES = [
    'ok',
    'unavailable',
] as const
export type ReadinessOutcome = (typeof READINESS_OUTCOME_VALUES)[number]

export const REBALANCE_MODE_VALUES = [
    'none',
    'scale_existing',
    'reject_if_over',
] as const
export type RebalanceMode = (typeof REBALANCE_MODE_VALUES)[number]

export const REPORT_PERIOD_VALUES = [
    'daily',
    'weekly',
    'monthly',
] as const
export type ReportPeriod = (typeof REPORT_PERIOD_VALUES)[number]

export const REQUEST_STATUS_VALUES = [
    'submitted',
    'triaging',
    'scoping',
    'approved',
    'task_created',
    'cancelled',
] as const
export type RequestStatus = (typeof REQUEST_STATUS_VALUES)[number]

export const RESUME_DECISION_VALUES = [
    'approve',
    'reject',
    'revise',
] as const
export type ResumeDecision = (typeof RESUME_DECISION_VALUES)[number]

export const REVIEW_VERDICT_VALUES = [
    'pass',
    'fail',
    'skip',
] as const
export type ReviewVerdict = (typeof REVIEW_VERDICT_VALUES)[number]

export const RISK_CLASSIFIER_TYPE_VALUES = [
    'default',
    'workload_adaptive',
    'operator_configurable',
    'time_based',
] as const
export type RiskClassifierType = (typeof RISK_CLASSIFIER_TYPE_VALUES)[number]

export const RISK_TOLERANCE_VALUES = [
    'low',
    'medium',
    'high',
] as const
export type RiskTolerance = (typeof RISK_TOLERANCE_VALUES)[number]

export const RULE_SEVERITY_VALUES = [
    'info',
    'warning',
    'critical',
] as const
export type RuleSeverity = (typeof RULE_SEVERITY_VALUES)[number]

export const SENIORITY_LEVEL_VALUES = [
    'junior',
    'mid',
    'senior',
    'lead',
    'principal',
    'director',
    'vp',
    'c_suite',
] as const
export type SeniorityLevel = (typeof SENIORITY_LEVEL_VALUES)[number]

export const SETTING_LEVEL_VALUES = [
    'basic',
    'advanced',
] as const
export type SettingLevel = (typeof SETTING_LEVEL_VALUES)[number]

export const SETTING_NAMESPACE_VALUES = [
    'api',
    'client',
    'company',
    'providers',
    'memory',
    'budget',
    'security',
    'coordination',
    'observability',
    'backup',
    'engine',
    'communication',
    'a2a',
    'integrations',
    'meta',
    'notifications',
    'simulations',
    'tools',
    'settings',
    'hr',
    'workers',
    'telemetry',
] as const
export type SettingNamespace = (typeof SETTING_NAMESPACE_VALUES)[number]

export const SETTING_SOURCE_VALUES = [
    'db',
    'env',
    'default',
] as const
export type SettingSource = (typeof SETTING_SOURCE_VALUES)[number]

export const SETTING_TYPE_VALUES = [
    'str',
    'int',
    'float',
    'bool',
    'enum',
    'json',
] as const
export type SettingType = (typeof SETTING_TYPE_VALUES)[number]

export const SKILL_PATTERN_VALUES = [
    'tool_wrapper',
    'generator',
    'reviewer',
    'inversion',
    'pipeline',
] as const
export type SkillPattern = (typeof SKILL_PATTERN_VALUES)[number]

export const STRATEGIC_OUTPUT_MODE_VALUES = [
    'option_expander',
    'advisor',
    'decision_maker',
    'context_dependent',
] as const
export type StrategicOutputMode = (typeof STRATEGIC_OUTPUT_MODE_VALUES)[number]

export const TASK_SOURCE_VALUES = [
    'internal',
    'client',
    'simulation',
] as const
export type TaskSource = (typeof TASK_SOURCE_VALUES)[number]

export const TASK_STATUS_VALUES = [
    'created',
    'assigned',
    'in_progress',
    'in_review',
    'completed',
    'blocked',
    'failed',
    'interrupted',
    'suspended',
    'cancelled',
    'rejected',
    'auth_required',
] as const
export type TaskStatus = (typeof TASK_STATUS_VALUES)[number]

export const TASK_STRUCTURE_VALUES = [
    'sequential',
    'parallel',
    'mixed',
] as const
export type TaskStructure = (typeof TASK_STRUCTURE_VALUES)[number]

export const TASK_TYPE_VALUES = [
    'development',
    'design',
    'research',
    'review',
    'meeting',
    'admin',
] as const
export type TaskType = (typeof TASK_TYPE_VALUES)[number]

export const TELEMETRY_STATUS_VALUES = [
    'enabled',
    'disabled',
] as const
export type TelemetryStatus = (typeof TELEMETRY_STATUS_VALUES)[number]

export const TERMINAL_ACCESS_VALUES = [
    'none',
    'restricted_commands',
    'full',
] as const
export type TerminalAccess = (typeof TERMINAL_ACCESS_VALUES)[number]

export const TIMEOUT_ACTION_TYPE_VALUES = [
    'wait',
    'approve',
    'deny',
    'escalate',
] as const
export type TimeoutActionType = (typeof TIMEOUT_ACTION_TYPE_VALUES)[number]

export const TOOL_ACCESS_LEVEL_VALUES = [
    'sandboxed',
    'restricted',
    'standard',
    'elevated',
    'custom',
] as const
export type ToolAccessLevel = (typeof TOOL_ACCESS_LEVEL_VALUES)[number]

export const TOOL_CATEGORY_VALUES = [
    'file_system',
    'code_execution',
    'version_control',
    'web',
    'database',
    'terminal',
    'design',
    'communication',
    'analytics',
    'deployment',
    'memory',
    'ontology',
    'mcp',
    'other',
] as const
export type ToolCategory = (typeof TOOL_CATEGORY_VALUES)[number]

export const TRAINING_PLAN_STATUS_VALUES = [
    'pending',
    'executed',
    'failed',
] as const
export type TrainingPlanStatus = (typeof TRAINING_PLAN_STATUS_VALUES)[number]

export const TREND_DIRECTION_VALUES = [
    'improving',
    'stable',
    'declining',
    'insufficient_data',
] as const
export type TrendDirection = (typeof TREND_DIRECTION_VALUES)[number]

export const TREND_METRIC_VALUES = [
    'tasks_completed',
    'spend',
    'active_agents',
    'success_rate',
] as const
export type TrendMetric = (typeof TREND_METRIC_VALUES)[number]

export const TREND_PERIOD_VALUES = [
    '7d',
    '30d',
    '90d',
] as const
export type TrendPeriod = (typeof TREND_PERIOD_VALUES)[number]

export const URGENCY_LEVEL_VALUES = [
    'critical',
    'high',
    'normal',
    'no_expiry',
] as const
export type UrgencyLevel = (typeof URGENCY_LEVEL_VALUES)[number]

export const VALIDATION_ERROR_CODE_VALUES = [
    'unreachable_node',
    'end_not_reachable',
    'conditional_missing_true',
    'conditional_missing_false',
    'conditional_extra_outgoing',
    'split_too_few_branches',
    'task_missing_title',
    'cycle_detected',
    'subworkflow_ref_missing',
    'subworkflow_version_unpinned',
    'subworkflow_not_found',
    'subworkflow_input_missing',
    'subworkflow_input_unknown',
    'subworkflow_input_type_mismatch',
    'subworkflow_output_missing',
    'subworkflow_output_unknown',
    'subworkflow_output_type_mismatch',
    'subworkflow_cycle_detected',
    'verification_missing_pass',
    'verification_missing_fail',
    'verification_missing_refer',
    'verification_duplicate_edge',
    'verification_extra_outgoing',
    'verification_edge_outside',
    'verification_missing_config',
] as const
export type ValidationErrorCode = (typeof VALIDATION_ERROR_CODE_VALUES)[number]

export const WORKFLOW_EDGE_TYPE_VALUES = [
    'sequential',
    'conditional_true',
    'conditional_false',
    'parallel_branch',
    'verification_pass',
    'verification_fail',
    'verification_refer',
] as const
export type WorkflowEdgeType = (typeof WORKFLOW_EDGE_TYPE_VALUES)[number]

export const WORKFLOW_EXECUTION_STATUS_VALUES = [
    'pending',
    'running',
    'completed',
    'failed',
    'cancelled',
] as const
export type WorkflowExecutionStatus = (typeof WORKFLOW_EXECUTION_STATUS_VALUES)[number]

export const WORKFLOW_NODE_EXECUTION_STATUS_VALUES = [
    'pending',
    'skipped',
    'task_created',
    'task_completed',
    'task_failed',
    'completed',
    'subworkflow_completed',
] as const
export type WorkflowNodeExecutionStatus = (typeof WORKFLOW_NODE_EXECUTION_STATUS_VALUES)[number]

export const WORKFLOW_NODE_TYPE_VALUES = [
    'start',
    'end',
    'task',
    'agent_assignment',
    'conditional',
    'parallel_split',
    'parallel_join',
    'subworkflow',
    'verification',
] as const
export type WorkflowNodeType = (typeof WORKFLOW_NODE_TYPE_VALUES)[number]

export const WORKFLOW_TYPE_VALUES = [
    'sequential_pipeline',
    'parallel_execution',
    'kanban',
    'agile_kanban',
] as const
export type WorkflowType = (typeof WORKFLOW_TYPE_VALUES)[number]

export const WORKFLOW_VALUE_TYPE_VALUES = [
    'string',
    'integer',
    'float',
    'boolean',
    'datetime',
    'json',
    'task_ref',
    'agent_ref',
] as const
export type WorkflowValueType = (typeof WORKFLOW_VALUE_TYPE_VALUES)[number]
