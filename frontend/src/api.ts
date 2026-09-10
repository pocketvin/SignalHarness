import type {
  ChangeDetail,
  ChangePage,
  DataSource,
  DemoMeta,
  InboxPayload,
  ProductPayload,
  ProfilePayload,
  ProjectDraft,
  RunMode,
  ScheduleItem,
  StreamRun,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const message = typeof detail === 'string' ? detail : detail?.message ?? JSON.stringify(detail ?? payload)
    throw new Error(message || `${response.status} ${response.statusText}`)
  }
  return payload as T
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  meta: () => request<DemoMeta>('/demo/meta'),
  profile: (projectId: string) => request<ProfilePayload>(`/projects/${encodeURIComponent(projectId)}/profile`),
  savePreference: (projectId: string, scopeType: string, scopeKey: string, importance: string) =>
    request<ProfilePayload>(
      `/projects/${encodeURIComponent(projectId)}/preferences`,
      jsonInit('POST', { scope_type: scopeType, scope_key: scopeKey, importance }),
    ),
  saveNaturalPreference: (projectId: string, instruction: string) =>
    request<ProfilePayload>(
      `/projects/${encodeURIComponent(projectId)}/preferences/natural-language`,
      jsonInit('POST', { instruction }),
    ),
  connectGithub: (url: string) => request<ProjectDraft>('/projects/connect/github', jsonInit('POST', { url })),
  connectLocal: (body: { name_hint?: string | null; manifests: Array<{ path: string; content: string }>; paths: string[] }) =>
    request<ProjectDraft>('/projects/connect', jsonInit('POST', body)),
  schedules: async (projectId: string) => {
    const payload = await request<{ schedules: ScheduleItem[] }>(`/projects/${encodeURIComponent(projectId)}/schedules`)
    return payload.schedules ?? []
  },
  createSchedule: (projectId: string, body: Record<string, unknown>) =>
    request<ScheduleItem>(`/projects/${encodeURIComponent(projectId)}/schedules`, jsonInit('POST', body)),
  deleteSchedule: (projectId: string, scheduleId: string) =>
    request<Record<string, unknown>>(`/projects/${encodeURIComponent(projectId)}/schedules/${encodeURIComponent(scheduleId)}`, { method: 'DELETE' }),
  inbox: (projectId: string) => request<InboxPayload>(`/projects/${encodeURIComponent(projectId)}/inbox?limit=50`),
  markInboxRead: (projectId: string, inboxId: string) =>
    request<Record<string, unknown>>(`/projects/${encodeURIComponent(projectId)}/inbox/${encodeURIComponent(inboxId)}/read`, { method: 'POST' }),
  startRun: (body: {
    project_id: string
    mode: RunMode
    data_source: DataSource
    provider_id?: string
    since_days: number
    max_events: number
    max_events_per_source: number
    fixture?: string
  }) => request<StreamRun>('/stream-runs', jsonInit('POST', body)),
  product: (runId: string) => request<ProductPayload>(`/runs/${encodeURIComponent(runId)}/product?top=12&all_limit=20`),
  changes: (runId: string, params: URLSearchParams) => request<ChangePage>(`/runs/${encodeURIComponent(runId)}/changes?${params}`),
  changeDetail: (runId: string, changeId: string) => request<ChangeDetail>(`/runs/${encodeURIComponent(runId)}/changes/${encodeURIComponent(changeId)}`),
  feedback: (body: { run_id: string; signal_id: string; label: string; note: string }) =>
    request<Record<string, unknown>>('/feedback', jsonInit('POST', body)),
  outcome: (projectId: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/projects/${encodeURIComponent(projectId)}/outcomes`, jsonInit('POST', body)),
}
