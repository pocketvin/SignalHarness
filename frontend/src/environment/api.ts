import type {
  ActivitySummary,
  Change,
  DeepDive,
  Home,
  LearningStatus,
  Meta,
  Page,
  Profile,
  Project,
  Report,
  Scan,
  TraceStep,
} from "./types";
export const encoded = encodeURIComponent;
export const projectBase = (id: string) =>
  `/intelligence/projects/${encoded(id)}`;
export const reportBase = (id: string, scan: string) =>
  `${projectBase(id)}/reports/${encoded(scan)}`;
export async function request<T>(
  url: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(url, {
    method,
    ...(body !== undefined
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
    signal: AbortSignal.timeout(60000),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `请求未完成（${response.status}），请重试。`,
    );
  }
  return data as T;
}
export const api = {
  meta: () => request<Meta>("/intelligence/meta"),
  home: (id: string) => request<Home>(projectBase(id)),
  profile: (id: string) => request<Profile>(`/projects/${encoded(id)}/profile`),
  report: (id: string, scan: string) => request<Report>(reportBase(id, scan)),
  changes: (id: string, scan: string, query: URLSearchParams) =>
    request<Page>(`${reportBase(id, scan)}/changes?${query}`),
  activitySummary: (id: string, scan: string) =>
    request<ActivitySummary>(`${reportBase(id, scan)}/activity-summary`),
  change: (id: string, scan: string, change: string) =>
    request<Change>(`${reportBase(id, scan)}/changes/${encoded(change)}`),
  scan: (id: string, body: unknown) =>
    request<Scan>(`${projectBase(id)}/scans`, "POST", body),
  cancelScan: (id: string, run: string) =>
    request<Scan>(`${projectBase(id)}/scans/${encoded(run)}`, "DELETE"),
  deep: (id: string, scan: string, change: string, retry = false) =>
    request<DeepDive>(
      `${reportBase(id, scan)}/changes/${encoded(change)}/deep-dive`,
      "POST",
      { retry },
    ),
  changeFeedback: (
    id: string,
    scan: string,
    change: string,
    label: "useful" | "not_useful" | "false_positive" | "too_generic",
    note = "",
  ) =>
    request<Record<string, unknown>>(
      `${reportBase(id, scan)}/changes/${encoded(change)}/feedback`,
      "POST",
      { label, note },
    ),
  changeOutcome: (
    id: string,
    scan: string,
    change: string,
    body: {
      impact_observed?: boolean;
      action_taken?: boolean;
      action_helpful?: boolean;
      resolved?: boolean;
      note?: string;
    },
  ) =>
    request<Record<string, unknown>>(
      `${reportBase(id, scan)}/changes/${encoded(change)}/outcome`,
      "POST",
      body,
    ),
  calibration: (id: string) =>
    request<LearningStatus>(`${projectBase(id)}/calibration`),
  preference: (id: string, scope: string, key: string, importance: string) =>
    request<Profile>(`/projects/${encoded(id)}/preferences`, "POST", {
      scope_type: scope,
      scope_key: key,
      importance,
    }),
  naturalPreference: (id: string, instruction: string) =>
    request<Profile>(
      `/projects/${encoded(id)}/preferences/natural-language`,
      "POST",
      { instruction },
    ),
  refreshArchitecture: (id: string) =>
    request<Profile>(`/projects/${encoded(id)}/architecture/refresh`, "POST"),
  connect: (url: string) =>
    request<{ project: Project }>("/projects/connect/github", "POST", { url }),
  audit: (id: string, scan: string) =>
    request<Record<string, unknown>>(`${reportBase(id, scan)}/audit`),
  trace: (id: string, scan: string) =>
    request<TraceStep[]>(`${reportBase(id, scan)}/trace`),
};
export function safeUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return /^https?:$/.test(url.protocol) && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function dateText(value: string | null, time = false): string {
  if (!value) return "未标注日期";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未标注日期";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    ...(time ? ({ hour: "2-digit", minute: "2-digit" } as const) : {}),
  }).format(date);
}
