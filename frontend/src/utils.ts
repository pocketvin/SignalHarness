import type { ChangeItem, Decision, Lang, TraceStep } from './types'

export const IMPACT_GROUPS = [
  'project_code',
  'dependency_version',
  'security',
  'api_protocol',
  'upstream_issue',
  'tech_news',
  'other',
] as const

export function formatDate(value?: string | null, lang: Lang = 'zh'): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function pct(value: number | null | undefined): string {
  return `${Math.round(Number(value ?? 0) * 100)}%`
}

export function impactGroupLabel(group: string | undefined, lang: Lang): string {
  const zh: Record<string, string> = {
    project_code: '项目代码', dependency_version: '依赖 / 版本', security: '安全', api_protocol: 'API / 协议',
    upstream_issue: '上游 Issue', tech_news: '技术生态', other: '其他',
  }
  const en: Record<string, string> = {
    project_code: 'Project code', dependency_version: 'Dependencies', security: 'Security', api_protocol: 'API / protocol',
    upstream_issue: 'Upstream issue', tech_news: 'Tech ecosystem', other: 'Other',
  }
  const map = lang === 'zh' ? zh : en
  return map[group ?? 'other'] ?? group ?? map.other
}

export function decisionLabel(decision: Decision | null | undefined, lang: Lang): string {
  const zh: Record<string, string> = { alert: '提醒', action_required: '需要行动', save: '保存', ignore: '忽略' }
  const en: Record<string, string> = { alert: 'Alert', action_required: 'Action required', save: 'Save', ignore: 'Ignore' }
  if (!decision) return lang === 'zh' ? '待深度分析' : 'Not deep analyzed'
  return (lang === 'zh' ? zh : en)[decision] ?? decision
}

export function decisionTone(decision: Decision | null | undefined): string {
  if (decision === 'alert' || decision === 'action_required') return 'alert'
  if (decision === 'save') return 'save'
  return 'neutral'
}

export function traceTone(trace: TraceStep): 'running' | 'success' | 'warn' | 'error' | 'skipped' {
  if (trace.status === 'running') return 'running'
  if (trace.status === 'error') return 'error'
  if (trace.fallback_used || trace.tool_errors?.length || trace.blocked_tools?.length) return 'warn'
  if (trace.status === 'skipped') return 'skipped'
  return 'success'
}

export function traceTitle(trace: TraceStep, lang: Lang): string {
  const agent = trace.agent_name ?? trace.agent ?? ''
  const zh: Record<string, string> = {
    ImpactActionAnalyzerAgent: '项目影响 + 行动判断', ProjectNarrativeAgent: '生成用户可读摘要', ContextEvidenceAgent: '证据核验',
    SignalSupervisorAgent: '模型路由', ImpactAnalystAgent: '项目影响分析', ActionPlannerAgent: '行动建议', LearningPolicyAgent: '学习观察',
  }
  const en: Record<string, string> = {
    ImpactActionAnalyzerAgent: 'Impact + action judgment', ProjectNarrativeAgent: 'Generate user narrative', ContextEvidenceAgent: 'Evidence verification',
    SignalSupervisorAgent: 'Model routing', ImpactAnalystAgent: 'Project impact analysis', ActionPlannerAgent: 'Action planning', LearningPolicyAgent: 'Learning observation',
  }
  if ((lang === 'zh' ? zh : en)[agent]) return (lang === 'zh' ? zh : en)[agent]
  const stepZh: Record<string, string> = {
    load_config: '读取配置', collect_signals: '采集来源', normalize: '标准化事件', time_window_filter: '时间窗口过滤', deduplicate: '变化去重',
    candidate_funnel: '项目相关性筛选', noise_filter: '噪声过滤', cluster_signals: '变化聚类', agent_loop_limits: '运行预算',
    harness_input_snapshot: '冻结分析输入', harness_deterministic_supervisor: '确定性路由', harness_deterministic_evidence: '确定性证据解析',
    adaptive_split_escalation: '升级为 Split 分析', agent_team_guardrail: '受控评分与权限', learning_deferred: '学习移出热路径',
    write_radar_digest: '写入雷达摘要', write_run_summary: '写入运行摘要', write_json_outputs: '写入审计数据',
  }
  const stepEn: Record<string, string> = {
    load_config: 'Load config', collect_signals: 'Collect sources', normalize: 'Normalize events', time_window_filter: 'Filter time window', deduplicate: 'Deduplicate changes',
    candidate_funnel: 'Project relevance funnel', noise_filter: 'Noise filter', cluster_signals: 'Cluster changes', agent_loop_limits: 'Runtime budget',
    harness_input_snapshot: 'Freeze analyzer input', harness_deterministic_supervisor: 'Deterministic route', harness_deterministic_evidence: 'Deterministic evidence',
    adaptive_split_escalation: 'Escalate to split analysis', agent_team_guardrail: 'Guarded score & permission', learning_deferred: 'Learning deferred',
    write_radar_digest: 'Write radar digest', write_run_summary: 'Write run summary', write_json_outputs: 'Write audit data',
  }
  return ((lang === 'zh' ? stepZh : stepEn)[trace.step] ?? agent) || trace.step || 'Trace'
}

export function stageForTrace(trace: TraceStep): 'collect' | 'route' | 'evidence' | 'analysis' | 'decision' | 'narrative' {
  const agent = trace.agent_name ?? trace.agent ?? ''
  if (['load_config', 'collect_signals', 'normalize', 'time_window_filter', 'deduplicate'].includes(trace.step)) return 'collect'
  if (['candidate_funnel', 'noise_filter', 'cluster_signals', 'agent_loop_limits', 'harness_input_snapshot', 'harness_deterministic_supervisor'].includes(trace.step) || agent === 'SignalSupervisorAgent') return 'route'
  if (trace.step === 'harness_deterministic_evidence' || agent === 'ContextEvidenceAgent' || trace.step.includes('evidence')) return 'evidence'
  if (['ImpactActionAnalyzerAgent', 'ImpactAnalystAgent', 'ActionPlannerAgent', 'SelectiveVerifierAgent'].includes(agent) || ['adaptive_split_escalation', 'repair_impact', 'repair_action'].includes(trace.step)) return 'analysis'
  if (['agent_team_guardrail', 'skipped_stage_audit_completion', 'learning_deferred'].includes(trace.step)) return 'decision'
  return 'narrative'
}

export function summarizeChange(item: ChangeItem): string {
  return item.what_changed_zh || item.summary_zh || '—'
}

export function safeExternalUrl(value?: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value, window.location.href)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}
