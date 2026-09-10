import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  AllFilters,
  ChangeDetail,
  DataSource,
  DemoMeta,
  InboxPayload,
  ProductPayload,
  ProfilePayload,
  ProjectDraft,
  RunMode,
  ScheduleItem,
  SourceTask,
  StreamRun,
  TraceStep,
} from '../types'

const SAFE_PROJECT_MANIFESTS = new Set([
  'pyproject.toml', 'package.json', 'requirements.txt', 'requirements-dev.txt', 'requirements.in',
  'cargo.toml', 'go.mod', 'uv.lock', 'package-lock.json',
])

function isSafeManifest(path: string): boolean {
  const base = path.split('/').at(-1)?.toLowerCase() ?? ''
  return SAFE_PROJECT_MANIFESTS.has(base) || (base.startsWith('requirements') && base.endsWith('.txt'))
}

export interface StartRunInput {
  dataSource: DataSource
  mode: RunMode
  providerId: string
  sinceDays: number
}

export function useSignalHarness() {
  const [meta, setMeta] = useState<DemoMeta | null>(null)
  const [projectId, setProjectId] = useState('')
  const [profile, setProfile] = useState<ProfilePayload | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [projectDraft, setProjectDraft] = useState<ProjectDraft | null>(null)
  const [projectStatus, setProjectStatus] = useState('')
  const [preferenceStatus, setPreferenceStatus] = useState('')
  const [schedules, setSchedules] = useState<ScheduleItem[]>([])
  const [inbox, setInbox] = useState<InboxPayload>({ items: [], count: 0, delivery: null })
  const [monitoringStatus, setMonitoringStatus] = useState('')
  const [run, setRun] = useState<StreamRun | null>(null)
  const [traces, setTraces] = useState<Array<TraceStep | undefined>>([])
  const [eventCount, setEventCount] = useState(0)
  const [sourceTasks, setSourceTasks] = useState<SourceTask[]>([])
  const [failedSources, setFailedSources] = useState<string[]>([])
  const [product, setProduct] = useState<ProductPayload | null>(null)
  const [productError, setProductError] = useState('')
  const [legacySignals, setLegacySignals] = useState<Array<Record<string, unknown>>>([])
  const [legacyAssessments, setLegacyAssessments] = useState<Array<Record<string, unknown>>>([])
  const [selectedDetail, setSelectedDetail] = useState<ChangeDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailStatus, setDetailStatus] = useState('')
  const [connection, setConnection] = useState<'idle' | 'connecting' | 'connected' | 'running' | 'complete' | 'error'>('idle')
  const [connectionMessage, setConnectionMessage] = useState('')
  const eventSourceRef = useRef<EventSource | null>(null)
  const currentRunIdRef = useRef<string | null>(null)

  const selectedProject = useMemo(
    () => meta?.projects.find((project) => project.id === projectId) ?? meta?.projects[0] ?? null,
    [meta, projectId],
  )

  const readyProviders = useMemo(() => meta?.providers.filter((provider) => provider.ready) ?? [], [meta])

  const refreshMeta = useCallback(async (preferredProjectId?: string) => {
    const payload = await api.meta()
    setMeta(payload)
    setProjectId((current) => {
      const preferred = preferredProjectId || current || payload.default_project_id
      return payload.projects.some((project) => project.id === preferred) ? preferred : payload.projects[0]?.id ?? ''
    })
    return payload
  }, [])

  const loadProfile = useCallback(async (id: string) => {
    if (!id) return
    setProfileLoading(true)
    try {
      setProfile(await api.profile(id))
    } finally {
      setProfileLoading(false)
    }
  }, [])

  const loadMonitoring = useCallback(async (id: string) => {
    if (!id) return
    try {
      const [scheduleItems, inboxPayload] = await Promise.all([api.schedules(id), api.inbox(id)])
      setSchedules(scheduleItems)
      setInbox(inboxPayload)
      setMonitoringStatus('')
    } catch (error) {
      setMonitoringStatus(String(error))
    }
  }, [])

  useEffect(() => {
    void refreshMeta().catch((error) => setProjectStatus(String(error)))
  }, [refreshMeta])

  useEffect(() => {
    if (!projectId) return
    setProfile(null)
    void loadProfile(projectId).catch((error) => setProjectStatus(String(error)))
    void loadMonitoring(projectId)
  }, [loadMonitoring, loadProfile, projectId])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && projectId) void loadMonitoring(projectId)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [loadMonitoring, projectId])

  useEffect(() => () => eventSourceRef.current?.close(), [])

  const savePreference = useCallback(async (scopeType: string, scopeKey: string, importance: string) => {
    if (!projectId) return
    setPreferenceStatus('…')
    try {
      setProfile(await api.savePreference(projectId, scopeType, scopeKey, importance))
      setPreferenceStatus('saved')
    } catch (error) {
      setPreferenceStatus(String(error))
      await loadProfile(projectId).catch(() => undefined)
    }
  }, [loadProfile, projectId])

  const saveNaturalPreference = useCallback(async (instruction: string) => {
    const text = instruction.trim()
    if (!projectId || !text) return false
    setPreferenceStatus('…')
    try {
      setProfile(await api.saveNaturalPreference(projectId, text))
      setPreferenceStatus('saved')
      return true
    } catch (error) {
      setPreferenceStatus(String(error))
      return false
    }
  }, [projectId])

  const connectGithub = useCallback(async (url: string) => {
    const value = url.trim()
    if (!value) return false
    setProjectStatus('loading')
    try {
      const draft = await api.connectGithub(value)
      setProjectDraft(draft)
      const id = draft.project?.id
      await refreshMeta(id)
      setProjectStatus('connected')
      return true
    } catch (error) {
      setProjectStatus(String(error))
      return false
    }
  }, [refreshMeta])

  const connectLocal = useCallback(async (files: FileList | null) => {
    const list = [...(files ?? [])]
    if (!list.length) return false
    const paths = list.map((file) => (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name).slice(0, 500)
    const selected = list.filter((file) => {
      const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
      return isSafeManifest(path) && file.size <= 1_000_000
    }).slice(0, 20)
    if (!selected.length) {
      setProjectStatus('No supported manifest found')
      return false
    }
    setProjectStatus('loading')
    try {
      const manifests = await Promise.all(selected.map(async (file) => ({
        path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
        content: await file.text(),
      })))
      const firstPath = paths[0] ?? ''
      const nameHint = firstPath.includes('/') ? firstPath.split('/')[0] : null
      const draft = await api.connectLocal({ name_hint: nameHint, manifests, paths })
      setProjectDraft(draft)
      await refreshMeta(draft.project?.id)
      setProjectStatus('connected')
      return true
    } catch (error) {
      setProjectStatus(String(error))
      return false
    }
  }, [refreshMeta])

  const loadProduct = useCallback(async (runId: string) => {
    try {
      const payload = await api.product(runId)
      if (currentRunIdRef.current && currentRunIdRef.current !== runId) return
      setProduct(payload)
      setProductError('')
    } catch (error) {
      setProductError(String(error))
    }
  }, [])

  const startRun = useCallback(async ({ dataSource, mode, providerId, sinceDays }: StartRunInput) => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    setRun(null)
    setTraces([])
    setEventCount(0)
    setSourceTasks([])
    setFailedSources([])
    setProduct(null)
    setProductError('')
    setLegacySignals([])
    setLegacyAssessments([])
    setSelectedDetail(null)
    setConnection('connecting')
    setConnectionMessage('')
    const body = {
      project_id: projectId,
      mode,
      data_source: dataSource,
      since_days: sinceDays,
      max_events: 12,
      max_events_per_source: 4,
      ...(dataSource === 'fixture' ? { fixture: 'examples/signal_harness/sample_events.json' } : {}),
      ...(mode === 'agent' ? { provider_id: providerId } : {}),
    }
    try {
      const created = await api.startRun(body)
      currentRunIdRef.current = created.run_id
      setRun(created)
      const source = new EventSource(created.events_url ?? `/stream-runs/${created.run_id}/events`)
      eventSourceRef.current = source
      source.onopen = () => setConnection('connected')
      source.onerror = () => {
        if (eventSourceRef.current) {
          setConnection('error')
          setConnectionMessage('reconnecting')
        }
      }
      const onEvent = (event: Event) => {
        setEventCount((count) => count + 1)
        const message = event as MessageEvent<string>
        let data: any = {}
        try { data = JSON.parse(message.data) } catch { return }
        if (message.type === 'run.started') {
          setRun(data)
          setConnection('running')
          return
        }
        if (message.type === 'trace.step' || message.type === 'trace.step.updated') {
          const trace = data.trace as TraceStep
          setTraces((items) => {
            const next = [...items]
            next[Number(data.index)] = trace
            return next
          })
          if (trace.step === 'collect_signals') {
            if (trace.source_tasks) setSourceTasks(trace.source_tasks)
            if (trace.failed_sources) setFailedSources(trace.failed_sources)
          }
          return
        }
        if (message.type === 'run.completed') {
          setRun(data.run)
          setLegacySignals(data.signals ?? [])
          setLegacyAssessments(data.assessments ?? [])
          setSourceTasks(data.source_tasks ?? [])
          setFailedSources(data.failed_sources ?? [])
          setConnection('complete')
          source.close()
          eventSourceRef.current = null
          void loadProduct(data.run?.run_id ?? created.run_id)
          void loadMonitoring(projectId)
          return
        }
        if (message.type === 'run.failed') {
          setRun(data.run)
          setConnection('error')
          setConnectionMessage(data.run?.error_class ?? 'error')
          source.close()
          eventSourceRef.current = null
        }
      }
      for (const name of ['run.created', 'run.started', 'trace.step', 'trace.step.updated', 'run.completed', 'run.failed']) {
        source.addEventListener(name, onEvent)
      }
      return created
    } catch (error) {
      setConnection('error')
      setConnectionMessage(String(error))
      throw error
    }
  }, [loadMonitoring, loadProduct, projectId])

  const reloadChanges = useCallback(async (filters: AllFilters, offset = 0) => {
    const runId = product?.scan_id ?? run?.run_id
    if (!runId) return
    const params = new URLSearchParams({
      offset: String(Math.max(0, offset)),
      limit: '20',
      query: filters.query.trim(),
      analysis: filters.analysis,
      sort: filters.sort,
    })
    if (filters.impactGroup !== 'all') params.set('impact_group', filters.impactGroup)
    const page = await api.changes(runId, params)
    setProduct((current) => current ? { ...current, all_changes: page } : current)
  }, [product?.scan_id, run?.run_id])

  const openChange = useCallback(async (changeId: string) => {
    const runId = product?.scan_id ?? run?.run_id
    if (!runId) return
    setDetailLoading(true)
    setDetailStatus('')
    try {
      setSelectedDetail(await api.changeDetail(runId, changeId))
    } finally {
      setDetailLoading(false)
    }
  }, [product?.scan_id, run?.run_id])

  const closeChange = useCallback(() => {
    setSelectedDetail(null)
    setDetailStatus('')
  }, [])

  const saveFeedback = useCallback(async (label: string, note: string) => {
    const runId = product?.scan_id ?? run?.run_id
    if (!runId || !selectedDetail?.event_id) return
    setDetailStatus('…')
    try {
      await api.feedback({ run_id: runId, signal_id: selectedDetail.event_id, label, note })
      setDetailStatus('feedback-saved')
    } catch (error) {
      setDetailStatus(String(error))
    }
  }, [product?.scan_id, run?.run_id, selectedDetail?.event_id])

  const saveOutcome = useCallback(async (facts: Record<string, boolean | null>, note: string) => {
    const runId = product?.scan_id ?? run?.run_id
    if (!runId || !projectId || !selectedDetail) return
    if (Object.values(facts).every((value) => value === null)) {
      setDetailStatus('missing-outcome')
      return
    }
    setDetailStatus('…')
    try {
      await api.outcome(projectId, { scan_id: runId, change_id: selectedDetail.change_id, ...facts, note })
      setDetailStatus('outcome-saved')
    } catch (error) {
      setDetailStatus(String(error))
    }
  }, [product?.scan_id, projectId, run?.run_id, selectedDetail])

  const createSchedule = useCallback(async (body: Record<string, unknown>) => {
    if (!projectId) return
    setMonitoringStatus('…')
    try {
      await api.createSchedule(projectId, body)
      await loadMonitoring(projectId)
      setMonitoringStatus('saved')
    } catch (error) {
      setMonitoringStatus(String(error))
    }
  }, [loadMonitoring, projectId])

  const deleteSchedule = useCallback(async (scheduleId: string) => {
    if (!projectId) return
    try {
      await api.deleteSchedule(projectId, scheduleId)
      await loadMonitoring(projectId)
    } catch (error) {
      setMonitoringStatus(String(error))
    }
  }, [loadMonitoring, projectId])

  const markInboxRead = useCallback(async (inboxId: string) => {
    if (!projectId) return
    try {
      await api.markInboxRead(projectId, inboxId)
      await loadMonitoring(projectId)
    } catch (error) {
      setMonitoringStatus(String(error))
    }
  }, [loadMonitoring, projectId])

  return {
    meta, projectId, setProjectId, selectedProject, readyProviders,
    profile, profileLoading, projectDraft, projectStatus, preferenceStatus,
    schedules, inbox, monitoringStatus, run, traces: traces.filter(Boolean) as TraceStep[], eventCount,
    sourceTasks, failedSources, product, productError, legacySignals, legacyAssessments,
    selectedDetail, detailLoading, detailStatus, connection, connectionMessage,
    refreshMeta, savePreference, saveNaturalPreference, connectGithub, connectLocal,
    loadMonitoring, createSchedule, deleteSchedule, markInboxRead,
    startRun, reloadChanges, openChange, closeChange, saveFeedback, saveOutcome,
  }
}
