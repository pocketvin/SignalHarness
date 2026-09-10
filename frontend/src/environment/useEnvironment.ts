import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  Change,
  DeepDive,
  Filters,
  History,
  Meta,
  Page,
  Profile,
  Progress,
  Report,
  Scan,
} from "./types";

const emptyFilters: Filters = {
  view: "relevant",
  query: "",
  directionId: "",
  offset: 0,
};
export function useEnvironment() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [projectId, setProjectId] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [history, setHistory] = useState<History[]>([]);
  const [page, setPage] = useState<Page | null>(null);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [loading, setLoading] = useState(false);
  const [pageLoading, setPageLoading] = useState(false);
  const [error, setError] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [detail, setDetail] = useState<Change | null>(null);
  const [deep, setDeep] = useState<DeepDive | null>(null);
  const [detailError, setDetailError] = useState("");
  const stream = useRef<EventSource | null>(null);
  const deepStream = useRef<EventSource | null>(null);
  const selectedProject = useRef(projectId);
  selectedProject.current = projectId;
  const openVersion = useRef(0);
  const reportVersion = useRef(0);
  const pageVersion = useRef(0);

  const refreshMeta = useCallback(async (select?: string) => {
    const data = await api.meta();
    setMeta(data);
    setProjectId((old) =>
      data.projects.some((p) => p.id === (select || old))
        ? select || old
        : data.default_project_id,
    );
  }, []);
  useEffect(() => {
    void refreshMeta().catch((e) => setError(String(e.message || e)));
  }, [refreshMeta]);

  const attach = useCallback((run: Scan, project: string) => {
    stream.current?.close();
    setScan(run);
    setProgress(run.progress);
    setReconnecting(false);
    const source = new EventSource(run.events_url);
    stream.current = source;
    source.onopen = () => {
      if (stream.current === source) setReconnecting(false);
    };
    source.onerror = () => {
      if (stream.current === source) setReconnecting(true);
    };
    const belongs = () =>
      selectedProject.current === project && stream.current === source;
    source.addEventListener("product.progress", (event) => {
      if (belongs()) setProgress(JSON.parse((event as MessageEvent).data));
    });
    source.addEventListener("run.started", () => {
      if (belongs())
        setScan((old) => (old ? { ...old, status: "running" } : old));
    });
    source.addEventListener("run.completed", () => {
      source.close();
      if (!belongs()) return;
      stream.current = null;
      setReconnecting(false);
      setScan((old) => (old ? { ...old, status: "success" } : old));
      void api
        .home(project)
        .then((home) => {
          if (selectedProject.current !== project) return;
          setReport(home.report);
          setHistory(home.history);
          setFilters(emptyFilters);
        })
        .catch((e) => setError(String(e.message || e)));
    });
    source.addEventListener("run.failed", () => {
      source.close();
      if (!belongs()) return;
      stream.current = null;
      setReconnecting(false);
      setScan((old) => (old ? { ...old, status: "error" } : old));
      setError("本次更新未完成。上一份报告仍可阅读，请重试。");
    });
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let stopped = false;
    stream.current?.close();
    stream.current = null;
    deepStream.current?.close();
    deepStream.current = null;
    openVersion.current++;
    reportVersion.current++;
    pageVersion.current++;
    setReport(null);
    setPage(null);
    setProfile(null);
    setDetail(null);
    setDeep(null);
    setProgress(null);
    setScan(null);
    setError("");
    setFilters(emptyFilters);
    setLoading(true);
    void Promise.all([api.home(projectId), api.profile(projectId)])
      .then(([home, context]) => {
        if (stopped) return;
        setReport(home.report);
        setHistory(home.history);
        setProfile(context);
        if (home.active_run) attach(home.active_run, projectId);
      })
      .catch((e) => {
        if (!stopped) setError(String(e.message || e));
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
      stream.current?.close();
    };
  }, [projectId, attach]);
  useEffect(
    () => () => {
      stream.current?.close();
      deepStream.current?.close();
    },
    [],
  );

  useEffect(() => {
    if (!report || report.project_id !== projectId) return;
    const version = ++pageVersion.current;
    setPageLoading(true);
    const params = new URLSearchParams({
      view: filters.view,
      query: filters.query,
      offset: String(filters.offset),
      limit: "25",
    });
    if (filters.directionId) params.set("direction_id", filters.directionId);
    void api
      .changes(projectId, report.scan_id, params)
      .then((data) => {
        if (version === pageVersion.current) setPage(data);
      })
      .catch((e) => {
        if (version === pageVersion.current) setError(String(e.message || e));
      })
      .finally(() => {
        if (version === pageVersion.current) setPageLoading(false);
      });
  }, [projectId, report, filters]);

  async function startScan(body: unknown) {
    const project = projectId;
    setError("");
    setProgress({ stage: "queued", message: "正在准备本期观察范围" });
    setScan({ run_id: "", status: "queued", progress: null, events_url: "" });
    try {
      const run = await api.scan(project, body);
      if (selectedProject.current === project) attach(run, project);
    } catch (e) {
      if (selectedProject.current === project) {
        setError(e instanceof Error ? e.message : String(e));
        setScan(null);
        setProgress(null);
      }
    }
  }
  async function chooseReport(id: string) {
    const version = ++reportVersion.current;
    openVersion.current++;
    setDetail(null);
    setDeep(null);
    deepStream.current?.close();
    try {
      const value = await api.report(projectId, id);
      if (version === reportVersion.current) {
        setReport(value);
        setFilters(emptyFilters);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  async function openChange(item: Change, retry = false) {
    if (!report) return;
    const version = ++openVersion.current;
    deepStream.current?.close();
    deepStream.current = null;
    setDetail(item);
    setDeep(null);
    setDetailError("");
    try {
      // Explicit click only. No GET, hover or evidence-preview starts a billable operation.
      const job = await api.deep(
        projectId,
        report.scan_id,
        item.change_id,
        retry,
      );
      if (version !== openVersion.current) return;
      setDeep(job);
      if (
        job.status === "complete" ||
        job.status === "error" ||
        !job.events_url
      )
        return;
      const source = new EventSource(job.events_url);
      deepStream.current = source;
      source.addEventListener("deep.updated", (event) => {
        if (version !== openVersion.current) return;
        const value = JSON.parse((event as MessageEvent).data) as DeepDive;
        setDeep(value);
        if (value.status === "complete" || value.status === "error") {
          source.close();
          deepStream.current = null;
        }
      });
    } catch (e) {
      if (version === openVersion.current)
        setDetailError(e instanceof Error ? e.message : String(e));
    }
  }
  const closeDetail = useCallback(() => {
    openVersion.current++;
    deepStream.current?.close();
    deepStream.current = null;
    setDetail(null);
    setDeep(null);
  }, []);
  async function savePreference(scope: string, key: string, value: string) {
    const project = projectId;
    const updated = await api.preference(project, scope, key, value);
    if (selectedProject.current === project) setProfile(updated);
  }
  async function naturalPreference(text: string) {
    const project = projectId;
    const updated = await api.naturalPreference(project, text);
    if (selectedProject.current === project) setProfile(updated);
  }
  return {
    meta,
    projectId,
    setProjectId,
    profile,
    report,
    history,
    page,
    filters,
    setFilters,
    loading,
    pageLoading,
    error,
    setError,
    scan,
    progress,
    reconnecting,
    detail,
    deep,
    detailError,
    startScan,
    chooseReport,
    openChange,
    closeDetail,
    savePreference,
    naturalPreference,
    refreshMeta,
  };
}
