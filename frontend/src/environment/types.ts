export interface Project {
  id: string;
  name: string;
  description?: string;
  watchlist?: { source_count?: number };
}
export interface Meta {
  projects: Project[];
  default_project_id: string;
  intelligence_ready: boolean;
}
export interface Evidence {
  evidence_id: string;
  event_revision_id: number;
  source_name: string;
  source_type: string;
  url: string;
  authority: string;
  excerpt: string;
  excerpt_truncated: boolean;
}
export interface Change {
  change_id: string;
  revision_id: string;
  title: string;
  entity: string;
  kind: string;
  published_at: string | null;
  corpus_role: "external_environment" | "project_activity";
  discovery_origin?: "watched" | "discovered";
  discovery_basis?: string;
  summary: string;
  what_changed: string;
  project_relation: string;
  relation_reason: string;
  attention: string;
  topics: string[];
  uncertainty: string;
  interpretation_status: string;
  relevant: boolean;
  featured: boolean;
  evidence: Evidence[];
}
export interface ActivitySample {
  change_id: string;
  text: string;
  published_at: string | null;
  kind: string;
}
export interface ActivityGroup {
  label: string;
  count: number;
  samples: ActivitySample[];
}
export interface ActivitySummary {
  total_count: number;
  groups: ActivityGroup[];
  other_count: number;
  type_counts: Record<string, number>;
}

export interface Direction {
  direction_id: string;
  revision_id: string;
  title: string;
  explanation: string;
  state: string;
  state_reason: string;
  supporting_change_ids: string[];
  contradicting_change_ids: string[];
  independent_source_count: number;
  authoritative_source_count: number;
  evidence_posture: "reported_issue" | "mixed" | "observed_change";
  project_connection: string;
  watch_next: string[];
  uncertainty: string;
}
export interface RadarItem {
  radar_type: "new_solution" | "emerging_direction";
  title: string;
  explanation: string;
  supporting_change_ids: string[];
  project_connection: string;
  why_now: string;
  uncertainty: string;
}
export interface Claim {
  text: string;
  supporting_change_ids: string[];
}
export interface Source {
  source_name: string;
  source_type: string;
  status: string;
  coverage_status: string;
  output_count: number;
}
export interface Report {
  contract_version: string;
  data_origin?: "live" | "replay";
  report_id: string;
  scan_id: string;
  project_id: string;
  profile_revision_id: string;
  created_at: string;
  window: { mode: string; from: string | null; to: string; first_use: boolean };
  status: string;
  coverage_status: string;
  counts: Record<string, number>;
  brief: Claim[];
  directions: Direction[];
  radar?: RadarItem[];
  risks: Claim[];
  opportunities: Claim[];
  featured: Change[];
  notices: string[];
  sources: Source[];
}
export interface Progress {
  stage: string;
  message: string;
  completed?: number;
  total?: number | null;
  status?: string;
}
export interface TraceStep {
  step: string;
  status: "running" | "success" | "error" | "skipped";
  agent?: string | null;
  agent_name?: string | null;
  input_count?: number | null;
  output_count?: number | null;
  duration_ms: number;
  provider?: string | null;
  model?: string | null;
  fallback_used?: boolean;
  cache_hit?: boolean | null;
  retry_count?: number;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  metadata?: Record<string, unknown>;
}
export interface TraceEvent {
  index: number;
  operation: "append" | "update";
  trace: TraceStep;
}
export interface Scan {
  run_id: string;
  status: "queued" | "running" | "success" | "error" | "cancelled";
  progress: Progress | null;
  events_url: string;
}
export interface History {
  scan_id: string;
  created_at: string;
  window_end: string;
}
export interface Home {
  report: Report | null;
  history: History[];
  active_run: Scan | null;
}
export interface Page {
  items: Change[];
  count: number;
  offset: number;
  limit: number;
  has_more: boolean;
}
export interface Usage {
  reference_id: string;
  path: string;
  line: number;
  excerpt: string;
  kind: string;
}
export interface DeepDive {
  job_id: string;
  status: string;
  progress: Progress;
  events_url?: string;
  error?: string | null;
  result: {
    summary: string;
    impact: string;
    evidence_ids: string[];
    usage_reference_ids: string[];
    verification_steps: string[];
    uncertainty: string;
  } | null;
  usage: {
    references: Usage[];
    files_checked: number;
    limited: boolean;
    notice: string;
  };
  sources: Array<Evidence & { fetch_status: string }>;
}
export interface Profile {
  profile_revision_id: string;
  effective_profile: {
    project_name?: string;
    purpose?: string;
    goal?: string;
    dependencies?: string[];
    protocols?: string[];
    providers?: string[];
    tech_stack?: string[];
    runtimes?: string[];
    repository?: {
      provider?: string;
      repo?: string;
      url?: string;
      default_branch?: string;
      private?: boolean;
    };
    discovery_profile?: {
      version?: string;
      project_domain?: string;
      problem_spaces?: string[];
      solution_categories?: string[];
      discovery_queries?: string[];
      exclusions?: string[];
    };
    architecture_snapshot?: {
      version?: string;
      coverage?: string;
      source_files_sampled?: number;
      source_file_cap?: number;
      subsystems?: Array<{
        name: string;
        paths: string[];
        sample_count?: number;
      }>;
      entrypoints?: Array<{ path: string; reason?: string }>;
      dependency_usage?: Array<{
        dependency: string;
        files: string[];
        occurrences_in_sample?: number;
        references?: Array<{ path: string; line: number; excerpt?: string }>;
      }>;
      static_edges?: Array<{ from: string; to: string; kind?: string }>;
      evidence_paths?: string[];
      limitations?: string[];
    };
  };
  preferences?: Array<{
    scope_type: string;
    scope_key: string;
    importance: string;
    active: boolean;
  }>;
}
export interface ReplayMetrics {
  true_positive: number;
  false_positive: number;
  missed_positive: number;
  true_negative: number;
  precision: number;
  recall: number;
}
export interface CalibrationReplay {
  dataset_version: string;
  project_id: string;
  labeled_count: number;
  minimum_labeled_required: number;
  old_metrics: ReplayMetrics;
  proposed_metrics: ReplayMetrics;
  false_positive_reduction: number;
  missed_positive_reduction: number;
  ranking_change_count: number;
  decision_change_count: number;
  notification_change_count: number;
  recommendation: string;
  promotion_allowed: boolean;
  reasons: string[];
}
export interface LearningCandidate {
  proposal_id?: string | null;
  reason?: string | null;
  expected_effect?: string | null;
  changed_keywords: string[];
  changed_sources: string[];
  requires_approval: boolean;
  policy_changes: Array<{ path: string; old: unknown; new: unknown }>;
}
export interface StagedLearningProposal {
  proposal_id: string;
  status: "staged" | "blocked" | "applied" | "rejected";
  created_at: string;
  applied_at?: string | null;
  approved: boolean;
  risk: {
    risk_level: "low" | "medium" | "high" | "critical";
    auto_stage_allowed: boolean;
    apply_requires_approval: boolean;
    replay_gate_passed: boolean;
    reasons: string[];
  };
}
export interface PolicyRevision {
  revision_id: string;
  proposal_id?: string | null;
  created_at?: string | null;
  rolled_back_at?: string | null;
}
export interface LearningStatus {
  project_id: string;
  dataset_version: string;
  feedback_count: number;
  outcome_count: number;
  episode_count: number;
  labeled_count: number;
  positive_count: number;
  negative_count: number;
  ambiguous_count: number;
  unlabeled_count: number;
  orphan_feedback_count: number;
  feedback_labels: Record<string, number>;
  minimum_labeled_required: number;
  labels_needed: number;
  ready_for_replay: boolean;
  learning_state:
    | "collecting"
    | "ready_for_replay"
    | "candidate_ready"
    | "candidate_blocked"
    | "review"
    | "applied";
  candidate: LearningCandidate | null;
  candidate_replay: CalibrationReplay | null;
  durable_replay: CalibrationReplay | null;
  staged_proposals: StagedLearningProposal[];
  policy_revisions: PolicyRevision[];
  revision_count: number;
  episodes: unknown[];
}
export type WindowMode = "since_last" | "24h" | "7d" | "30d" | "custom";
export interface Filters {
  view: string;
  query: string;
  directionId: string;
  offset: number;
}
