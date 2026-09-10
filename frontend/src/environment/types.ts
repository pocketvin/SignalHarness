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
  project_connection: string;
  watch_next: string[];
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
export interface Scan {
  run_id: string;
  status: string;
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
  };
  preferences?: Array<{
    scope_type: string;
    scope_key: string;
    importance: string;
    active: boolean;
  }>;
}
export type WindowMode = "since_last" | "24h" | "7d" | "30d" | "custom";
export interface Filters {
  view: string;
  query: string;
  directionId: string;
  offset: number;
}
