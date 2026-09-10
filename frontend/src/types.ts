export type Lang = 'zh' | 'en'
export type RunMode = 'demo' | 'mock-agent' | 'agent'
export type DataSource = 'live' | 'fixture'
export type Decision = 'ignore' | 'save' | 'alert' | 'action_required' | string

export interface ProviderOption {
  id: string
  label: string
  model: string
  ready: boolean
  warning?: string | null
  reason?: string | null
}

export interface ProjectOption {
  id: string
  name: string
  watchlist?: {
    source_count?: number
    sources?: Array<{ name?: string; type?: string }>
  }
}

export interface DemoMeta {
  projects: ProjectOption[]
  providers: ProviderOption[]
  default_project_id: string
  default_provider_id?: string | null
  regression: {
    suite: string
    cases: number
    passed: boolean
    decision_accuracy: number
    category_accuracy: number
    priority_precision: number
    priority_recall: number
  }
  mcp: {
    tool_count: number
    read_only_tool_count: number
    write_tool_count: number
    tools: string[]
  }
}

export interface ProjectProfile {
  project_name?: string
  purpose?: string
  goal?: string
  tech_stack?: string[]
  runtimes?: string[]
  protocols?: string[]
  providers?: string[]
  dependencies?: string[]
  critical_modules?: string[]
  monitored_ecosystem?: string[]
  competitors?: string[]
  unknowns?: string[]
}

export interface Preference {
  active?: boolean
  scope_type: string
  scope_key: string
  importance: string
}

export interface ProfilePayload {
  profile_revision_id?: string
  effective_profile?: ProjectProfile
  preferences?: Preference[]
}

export interface SourceTask {
  source_name: string
  source_type?: string
  status: string
  output_count?: number
  error?: string | null
  coverage_status?: string
}

export interface ReasoningItem {
  event_id?: string
  title?: string
  summary?: string
  risk_level?: string
  semantic_relevance?: number
  confidence?: number
  source_quality?: string
  uncertainty?: string
  affected_modules?: string[]
  unsupported_claims?: string[]
  recommended_actions?: string[]
  recommended_actions_zh?: string[]
  what_changed_zh?: string
}

export interface TraceStep {
  step: string
  agent?: string | null
  agent_name?: string | null
  status: string
  mode?: string | null
  provider?: string | null
  model?: string | null
  model_profile?: string | null
  prompt_version?: string | null
  input_event_id?: string | null
  output_schema?: string | null
  schema_valid?: boolean | null
  fallback_used?: boolean
  duration_ms?: number
  input_count?: number | null
  output_count?: number | null
  tools_requested?: string[]
  tools_executed?: string[]
  tool_errors?: string[]
  blocked_tools?: string[]
  permission_checks?: string[]
  retry_count?: number
  schema_error?: string | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  estimated_cost_usd?: number | null
  error?: string | null
  detail?: string
  source_tasks?: SourceTask[]
  failed_sources?: string[]
  metadata?: {
    failure_kind?: string
    reasoning_version?: string
    reasoning_disclosure?: string
    reasoning_state?: string
    reasoning_summary?: string
    reasoning_items?: ReasoningItem[]
    [key: string]: unknown
  }
}

export interface StreamRun {
  run_id: string
  status: string
  mode: RunMode
  source_mode: DataSource
  provider_id?: string | null
  model?: string | null
  project_id: string
  project_name: string
  events_url?: string
  source_summary?: {
    sources?: number
    successful_sources?: number
    collected_events?: number
  }
  error_class?: string | null
}

export interface ChangeItem {
  change_id: string
  rank: number
  change_type?: string
  impact_group?: string
  impact_group_zh?: string
  summary_zh: string
  what_changed_zh?: string
  why_relevant_zh?: string
  recommended_actions_zh?: string[]
  project_impact_zh?: string
  decision?: Decision | null
  impact_score?: number | null
  selected_for_analysis?: boolean
  source_type?: string
  source_name: string
  published_at?: string | null
  evidence_count?: number
}

export interface ProductReport {
  scan_id: string
  project_id?: string
  profile_revision_id?: string
  coverage_status?: string
  summary_zh?: string
  themes?: string[]
  stats?: {
    all_change_count?: number
    analyzed_count?: number
    unanalyzed_count?: number
    high_priority_count?: number
    source_count?: number
    decision_counts?: Record<string, number>
    impact_group_counts?: Record<string, number>
  }
}

export interface ChangePage {
  scan_id: string
  items: ChangeItem[]
  count: number
  all_count: number
  offset: number
  limit: number
  returned: number
  has_more: boolean
  sort: string
}

export interface ProductPayload {
  scan_id: string
  report: ProductReport
  top_changes: ChangeItem[]
  all_changes: ChangePage
}

export interface ChangeDetail extends ChangeItem {
  event_id?: string
  event_revision_id?: number
  affected_modules?: string[]
  recommended_actions?: string[]
  before_after?: { before?: string; after?: string } | null
  evidence?: Array<{ url?: string; source_name?: string; source_quality?: string }>
  audit?: Record<string, unknown>
}

export interface ScheduleItem {
  schedule_id: string
  cadence: string
  timezone?: string
  local_time?: string | null
  mode: RunMode
  provider_id?: string | null
  enabled: boolean
  next_run_at?: string | null
  checkpoint_at?: string | null
  last_status?: string | null
}

export interface InboxItem {
  inbox_id: string
  title: string
  created_at?: string
  read_at?: string | null
  category?: string
  decision?: Decision
  impact_score?: number
  payload?: { links?: { report?: string } }
}

export interface InboxPayload {
  items: InboxItem[]
  count: number
  delivery?: {
    signed_webhook_configured?: boolean
    destination_key?: string | null
  } | null
}

export interface AllFilters {
  query: string
  impactGroup: string
  analysis: string
  sort: string
}

export interface ProjectDraft {
  project?: ProjectOption
  profile?: { effective_profile?: ProjectProfile }
  project_profile?: ProjectProfile
}
