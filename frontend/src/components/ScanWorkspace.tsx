import { useMemo, useRef, useState } from 'react'
import { FolderOpen, GitBranch, Play, Radar, Sparkles, Wifi, WifiOff } from 'lucide-react'
import { t } from '../i18n'
import type { DataSource, Lang, ProjectOption, ProviderOption, RunMode, StreamRun, TraceStep } from '../types'
import { TracePanel } from './TracePanel'

interface ScanWorkspaceProps {
  lang: Lang
  projects: ProjectOption[]
  projectId: string
  onProjectChange: (id: string) => void
  providers: ProviderOption[]
  traces: TraceStep[]
  eventCount: number
  run: StreamRun | null
  connection: string
  connectionMessage: string
  projectStatus: string
  onConnectGithub: (url: string) => Promise<boolean>
  onConnectLocal: (files: FileList | null) => Promise<boolean>
  onStart: (input: { dataSource: DataSource; mode: RunMode; providerId: string; sinceDays: number }) => Promise<unknown>
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  const caption = htmlFor ? (
    <label className="mb-1.5 block text-[9px] font-semibold uppercase tracking-[.05em] text-slate-400" htmlFor={htmlFor}>{label}</label>
  ) : (
    <span className="mb-1.5 block text-[9px] font-semibold uppercase tracking-[.05em] text-slate-400">{label}</span>
  )
  return <div>{caption}{children}</div>
}

function Segmented({
  value,
  items,
  onChange,
  label,
  columns,
}: {
  value: string
  items: Array<{ value: string; label: string }>
  onChange: (value: string) => void
  label: string
  columns?: number
}) {
  return (
    <div
      className="grid gap-1 rounded-lg border border-slate-200 bg-slate-100/80 p-1"
      role="group"
      aria-label={label}
      style={{ gridTemplateColumns: `repeat(${columns ?? items.length}, minmax(0, 1fr))` }}
    >
      {items.map((item) => (
        <button
          className={`min-h-8 rounded-md px-2 text-[10px] font-semibold transition-[background-color,color,box-shadow] ${
            value === item.value
              ? 'bg-white text-slate-950 shadow-sm'
              : 'text-slate-500 hover:bg-white/55 hover:text-slate-800'
          }`}
          type="button"
          aria-pressed={value === item.value}
          key={item.value}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

const selectClass = 'h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] text-slate-800 outline-none transition-colors hover:border-slate-300 focus:border-blue-500 focus:ring-3 focus:ring-blue-500/10'
const inputClass = 'h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[11px] text-slate-800 outline-none transition-colors placeholder:text-slate-400 hover:border-slate-300 focus:border-blue-500 focus:ring-3 focus:ring-blue-500/10'

export function ScanWorkspace(props: ScanWorkspaceProps) {
  const {
    lang,
    projects,
    projectId,
    onProjectChange,
    providers,
    traces,
    eventCount,
    run,
    connection,
    connectionMessage,
    projectStatus,
    onConnectGithub,
    onConnectLocal,
    onStart,
  } = props
  const [dataSource, setDataSource] = useState<DataSource>('live')
  const [mode, setMode] = useState<RunMode>('mock-agent')
  const [providerId, setProviderId] = useState('')
  const [windowValue, setWindowValue] = useState('14')
  const [customDays, setCustomDays] = useState(60)
  const [githubUrl, setGithubUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const selectedProviderId = useMemo(() => {
    if (providerId && providers.some((provider) => provider.id === providerId)) return providerId
    return providers[0]?.id ?? ''
  }, [providerId, providers])
  const scanning = busy || run?.status === 'queued' || run?.status === 'running' || connection === 'connecting' || connection === 'running'
  const sinceDays = windowValue === 'custom' ? customDays : Number(windowValue)

  async function start() {
    if (!projectId || !Number.isInteger(sinceDays) || sinceDays < 1 || sinceDays > 3650) return
    setBusy(true)
    try {
      await onStart({ dataSource, mode, providerId: selectedProviderId, sinceDays })
    } finally {
      setBusy(false)
    }
  }

  async function importGithub() {
    if (!githubUrl.trim()) return
    setBusy(true)
    try {
      const ok = await onConnectGithub(githubUrl)
      if (ok) setGithubUrl('')
    } finally {
      setBusy(false)
    }
  }

  const statusActive = connection === 'running' || connection === 'connected' || connection === 'complete'

  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(350px,.68fr)_minmax(600px,1.32fr)]">
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-4 py-3.5">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-900">
            <Radar className="h-4 w-4 text-blue-600" />
            {t(lang, 'scan')}
          </div>
          <p className="mt-1 text-[9px] leading-4 text-slate-500">
            {lang === 'zh'
              ? '选择项目与运行策略，然后把同一条 Workflow 交给 Trace 审计。'
              : 'Choose the project and execution strategy, then audit the same workflow through Trace.'}
          </p>
        </div>

        <div className="space-y-3.5 px-4 py-4">
          <Field label={t(lang, 'project')} htmlFor="projectSelect">
            <select id="projectSelect" className={selectClass} value={projectId} onChange={(event) => onProjectChange(event.target.value)}>
              {projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
            </select>
          </Field>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
            <Field label={t(lang, 'source')}>
              <div id="dataSource">
                <Segmented
                  label={t(lang, 'source')}
                  value={dataSource}
                  items={[
                    { value: 'live', label: t(lang, 'live') },
                    { value: 'fixture', label: t(lang, 'fixture') },
                  ]}
                  onChange={(value) => setDataSource(value as DataSource)}
                />
              </div>
            </Field>
            <Field label={t(lang, 'mode')}>
              <div id="analysisMode">
                <Segmented
                  label={t(lang, 'mode')}
                  value={mode}
                  columns={3}
                  items={[
                    { value: 'mock-agent', label: lang === 'zh' ? '离线' : 'Offline' },
                    { value: 'agent', label: lang === 'zh' ? '真实模型' : 'Real' },
                    { value: 'demo', label: lang === 'zh' ? '确定性' : 'Deterministic' },
                  ]}
                  onChange={(value) => setMode(value as RunMode)}
                />
              </div>
            </Field>
          </div>

          {mode === 'agent' && (
            <Field label={t(lang, 'model')} htmlFor="providerSelect">
              <select id="providerSelect" className={selectClass} value={selectedProviderId} onChange={(event) => setProviderId(event.target.value)}>
                {providers.map((provider) => (
                  <option value={provider.id} key={provider.id}>
                    {provider.label} · {provider.model}{provider.warning ? ' ⚠' : ''}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {dataSource === 'live' && (
            <Field label={t(lang, 'window')}>
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div id="sinceDays">
                  <Segmented
                    label={t(lang, 'window')}
                    value={windowValue}
                    columns={4}
                    items={[
                      { value: '7', label: '7d' },
                      { value: '14', label: '14d' },
                      { value: '30', label: '30d' },
                      { value: 'custom', label: t(lang, 'custom') },
                    ]}
                    onChange={setWindowValue}
                  />
                </div>
                {windowValue === 'custom' && (
                  <input
                    id="customDays"
                    className={`${inputClass} w-full sm:w-24`}
                    aria-label={t(lang, 'days')}
                    type="number"
                    min={1}
                    max={3650}
                    value={customDays}
                    onChange={(event) => setCustomDays(Number(event.target.value))}
                  />
                )}
              </div>
            </Field>
          )}

          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] xl:grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_auto]">
            <button
              id="runBtn"
              className="group flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-[11px] font-semibold text-white transition-[background-color,transform] hover:bg-blue-700 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
              type="button"
              disabled={scanning || !projectId || (mode === 'agent' && !selectedProviderId)}
              onClick={() => void start()}
            >
              {scanning ? <Sparkles className="h-3.5 w-3.5 animate-pulse" /> : <Play className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />}
              {scanning ? t(lang, 'scanning') : t(lang, 'scan')}
            </button>
            <div className="flex min-h-10 min-w-0 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-[9px] text-slate-500" role="status" aria-live="polite">
              {connection === 'error'
                ? <WifiOff className="h-3.5 w-3.5 shrink-0 text-rose-500" />
                : <Wifi className={`h-3.5 w-3.5 shrink-0 ${statusActive ? 'text-emerald-600' : 'text-slate-400'}`} />}
              <span className="max-w-44 truncate">{connectionMessage || (run ? `${run.run_id} · ${run.status}` : t(lang, 'ready'))}</span>
            </div>
          </div>
        </div>

        <details className="border-t border-slate-100 bg-slate-50/35">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-[9px] font-semibold text-slate-500 hover:bg-slate-50 [&::-webkit-details-marker]:hidden">
            <span>{lang === 'zh' ? '连接其他项目' : 'Connect another project'}</span>
            <span className="font-mono text-[8px] text-slate-400">GitHub / Local</span>
          </summary>
          <div className="space-y-3 border-t border-slate-100 px-4 py-4">
            <div className="flex gap-2">
              <label className="sr-only" htmlFor="github-url">GitHub repository URL</label>
              <input
                id="github-url"
                className={inputClass}
                type="url"
                autoComplete="off"
                spellCheck={false}
                placeholder={t(lang, 'githubPlaceholder')}
                value={githubUrl}
                onChange={(event) => setGithubUrl(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') void importGithub() }}
              />
              <button className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[9px] font-semibold text-slate-700 hover:border-slate-300" type="button" onClick={() => void importGithub()}>
                <GitBranch className="h-3.5 w-3.5" />{t(lang, 'connectGithub')}
              </button>
            </div>
            <button
              className="flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 bg-white text-[9px] font-semibold text-slate-600 hover:border-blue-300 hover:text-blue-700"
              type="button"
              onClick={() => {
                if (fileRef.current) {
                  fileRef.current.setAttribute('webkitdirectory', '')
                  fileRef.current.value = ''
                  fileRef.current.click()
                }
              }}
            >
              <FolderOpen className="h-3.5 w-3.5" />{t(lang, 'connectLocal')}
            </button>
            <input ref={fileRef} type="file" multiple hidden onChange={(event) => void onConnectLocal(event.target.files)} />
            {!!projectStatus && (
              <p className={`text-[9px] leading-4 ${projectStatus === 'connected' ? 'text-emerald-700' : projectStatus === 'loading' ? 'text-blue-700' : 'text-rose-600'}`}>
                {projectStatus}
              </p>
            )}
          </div>
        </details>
      </div>

      <div id="liveTrace"><TracePanel traces={traces} eventCount={eventCount} lang={lang} compact /></div>
    </section>
  )
}
