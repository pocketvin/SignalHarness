import { useMemo, useState } from 'react'
import { Activity, ArrowDown, Languages, Radar } from 'lucide-react'
import { t } from './i18n'
import type { Lang } from './types'
import { pct } from './utils'
import { useSignalHarness } from './hooks/useSignalHarness'
import { ChangeDrawer } from './components/ChangeDrawer'
import { Monitoring } from './components/Monitoring'
import { ProjectContext } from './components/ProjectContext'
import { Results } from './components/Results'
import { ScanWorkspace } from './components/ScanWorkspace'
import { TracePanel } from './components/TracePanel'

export default function App() {
  const [lang, setLang] = useState<Lang>('zh')
  const sh = useSignalHarness()
  const sourceCount = sh.selectedProject?.watchlist?.source_count ?? sh.selectedProject?.watchlist?.sources?.length ?? 0
  const analyzed = sh.product?.report.stats?.analyzed_count ?? 0
  const allChanges = sh.product?.report.stats?.all_change_count ?? 0
  const alerts = sh.product?.report.stats?.high_priority_count ?? 0
  const activeRun = sh.run?.status === 'queued' || sh.run?.status === 'running'

  const quickStats = useMemo(() => [
    [lang === 'zh' ? '监控来源' : 'Sources', sourceCount],
    [lang === 'zh' ? '完整变化' : 'Changes', allChanges],
    [lang === 'zh' ? '深度分析' : 'Analyzed', analyzed],
    [lang === 'zh' ? '提醒' : 'Alerts', alerts],
  ], [alerts, allChanges, analyzed, lang, sourceCount])

  if (!sh.meta) {
    return (
      <div className="grid min-h-screen place-items-center bg-canvas">
        <div className="flex items-center gap-3 text-[12px] text-slate-500"><Activity className="h-4 w-4 animate-pulse text-blue-600" />SignalHarness runtime…</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-canvas text-slate-900 selection:bg-blue-200 selection:text-slate-950">
      <a className="skip-link" href="#main">{lang === 'zh' ? '跳到主要内容' : 'Skip to main content'}</a>
      <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-canvas/92 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[1500px] items-center justify-between gap-4 px-5 sm:px-8 xl:px-10">
          <a className="flex min-w-0 items-center gap-2.5" href="#top" aria-label="SignalHarness home">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-950 text-white shadow-sm"><Radar className="h-4 w-4" /></span>
            <span className="min-w-0"><b className="block truncate text-[12px] tracking-[-.01em]">SignalHarness</b><span className="block truncate text-[8px] text-slate-400">{t(lang, 'brandSub')}</span></span>
          </a>
          <nav className="hidden items-center gap-5 text-[9px] font-medium text-slate-500 md:flex" aria-label="Primary">
            <a className="hover:text-slate-950" href="#workspace">{lang === 'zh' ? '运行' : 'Runtime'}</a>
            <a className="hover:text-slate-950" href="#results">{lang === 'zh' ? '情报' : 'Intelligence'}</a>
            <a className="hover:text-slate-950" href="#audit">{lang === 'zh' ? '审计' : 'Audit'}</a>
          </nav>
          <div className="flex items-center gap-2">
            <span className={`hidden rounded-full border px-2.5 py-1 font-mono text-[8px] sm:inline-flex ${activeRun ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-slate-200 bg-white text-slate-500'}`}>{activeRun ? (lang === 'zh' ? 'RUNNING' : 'RUNNING') : 'READY'}</span>
            <button className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[9px] font-semibold text-slate-600 hover:border-slate-300 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" type="button" onClick={() => setLang((current) => current === 'zh' ? 'en' : 'zh')}><Languages className="h-3.5 w-3.5" />{lang === 'zh' ? 'EN' : '中文'}</button>
          </div>
        </div>
      </header>

      <section id="top" className="border-b border-slate-200 bg-surface">
        <div className="mx-auto grid max-w-[1500px] items-end gap-5 px-5 py-5 sm:px-8 lg:grid-cols-[minmax(0,1fr)_auto] xl:px-10">
          <div className="min-w-0">
            <div className="font-mono text-[8px] font-semibold tracking-[.12em] text-blue-700">PROJECT ENVIRONMENT INTELLIGENCE · {sh.selectedProject?.name ?? 'SignalHarness'}</div>
            <h1 className="mt-2 text-balance text-[clamp(24px,2.6vw,36px)] font-semibold leading-[1.08] tracking-[-.035em] text-slate-950">{t(lang, 'headline')}</h1>
            <p className="mt-2 max-w-3xl text-pretty text-[11px] leading-5 text-slate-500">{sh.profile?.effective_profile?.purpose || t(lang, 'subhead')}</p>
          </div>
          <div className="grid min-w-[340px] grid-cols-4 divide-x divide-slate-200 border-y border-slate-200 py-2.5">
            {quickStats.map(([label, value]) => <div className="px-3 first:pl-0 last:pr-0" key={String(label)}><span className="block text-[8px] text-slate-400">{label}</span><b className="mt-0.5 block font-mono text-[17px] font-semibold tracking-[-.03em] text-slate-900">{value}</b></div>)}
          </div>
        </div>
      </section>

      <main id="main" tabIndex={-1}>
        <section id="workspace" className="mx-auto max-w-[1500px] px-5 py-5 sm:px-8 xl:px-10">
          <ScanWorkspace
            lang={lang}
            projects={sh.meta.projects}
            projectId={sh.projectId}
            onProjectChange={sh.setProjectId}
            providers={sh.readyProviders}
            traces={sh.traces}
            eventCount={sh.eventCount}
            run={sh.run}
            connection={sh.connection}
            connectionMessage={sh.connectionMessage}
            projectStatus={sh.projectStatus}
            onConnectGithub={sh.connectGithub}
            onConnectLocal={sh.connectLocal}
            onStart={sh.startRun}
          />
        </section>

        <ProjectContext
          lang={lang}
          profile={sh.profile}
          loading={sh.profileLoading}
          status={sh.preferenceStatus}
          onSavePreference={sh.savePreference}
          onSaveNaturalPreference={sh.saveNaturalPreference}
        />

        <Monitoring
          lang={lang}
          schedules={sh.schedules}
          inbox={sh.inbox}
          providers={sh.readyProviders}
          status={sh.monitoringStatus}
          onRefresh={() => sh.loadMonitoring(sh.projectId)}
          onCreate={sh.createSchedule}
          onDelete={sh.deleteSchedule}
          onRead={sh.markInboxRead}
        />

        <Results
          lang={lang}
          product={sh.product}
          productError={sh.productError}
          run={sh.run}
          sourceTasks={sh.sourceTasks}
          legacySignals={sh.legacySignals}
          onReloadChanges={sh.reloadChanges}
          onOpenChange={(changeId) => void sh.openChange(changeId)}
        />

        <section id="audit" className="border-t border-slate-200 bg-audit">
          <div className="mx-auto max-w-[1500px] px-5 py-9 sm:px-8 xl:px-10">
            <details id="auditDetails" className="group">
              <summary className="flex cursor-pointer list-none items-end justify-between gap-4 border-b border-slate-300 pb-4 [&::-webkit-details-marker]:hidden">
                <div><div className="flex items-center gap-2 text-[11px] font-semibold text-slate-900"><Activity className="h-4 w-4 text-blue-600" />{t(lang, 'audit')}</div><p className="mt-1 text-[10px] text-slate-500">{t(lang, 'auditSub')}</p></div>
                <div className="flex items-center gap-5"><span className="font-mono text-[9px] text-slate-400">{sh.eventCount} SSE · {sh.traces.length} Trace</span><ArrowDown className="h-4 w-4 text-slate-400 transition-transform group-open:rotate-180" /></div>
              </summary>
              <div className="mt-5"><TracePanel traces={sh.traces} eventCount={sh.eventCount} lang={lang} /></div>
            </details>

            <div className="mt-7 grid gap-6 border-t border-slate-300 pt-6 lg:grid-cols-2">
              <section>
                <span className="section-label">{t(lang, 'regression')}</span>
                <div className="mt-3 grid grid-cols-5 divide-x divide-slate-300 border-y border-slate-300 py-3">
                  <AuditMetric label={lang === 'zh' ? '案例' : 'Cases'} value={sh.meta.regression.cases} />
                  <AuditMetric label={lang === 'zh' ? '决策' : 'Decision'} value={pct(sh.meta.regression.decision_accuracy)} />
                  <AuditMetric label={lang === 'zh' ? '分类' : 'Category'} value={pct(sh.meta.regression.category_accuracy)} />
                  <AuditMetric label="Precision" value={pct(sh.meta.regression.priority_precision)} />
                  <AuditMetric label="Recall" value={pct(sh.meta.regression.priority_recall)} />
                </div>
              </section>
              <section>
                <span className="section-label">{t(lang, 'mcp')}</span>
                <p className="mt-2 text-[10px] text-slate-500">{sh.meta.mcp.tool_count} tools · {sh.meta.mcp.read_only_tool_count} read-only · {sh.meta.mcp.write_tool_count} scan starter</p>
                <div className="mt-3 flex flex-wrap gap-1.5">{sh.meta.mcp.tools.map((tool) => <span className="rounded-md border border-slate-300 bg-white/60 px-2 py-1 font-mono text-[8px] text-slate-500" key={tool}>{tool.replace('signalharness_', '')}</span>)}</div>
              </section>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-canvas">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-2 px-5 py-5 text-[8px] text-slate-400 sm:flex-row sm:items-center sm:justify-between sm:px-8 xl:px-10">
          <span>{t(lang, 'aiDisclosure')}</span><span className="font-mono">CLI · REST · SSE · MCP · React/Tailwind</span>
        </div>
      </footer>

      <ChangeDrawer
        lang={lang}
        detail={sh.selectedDetail}
        loading={sh.detailLoading}
        status={sh.detailStatus}
        onClose={sh.closeChange}
        onFeedback={sh.saveFeedback}
        onOutcome={sh.saveOutcome}
      />
    </div>
  )
}

function AuditMetric({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="px-3 first:pl-0"><span className="block text-[8px] text-slate-400">{label}</span><b className="mt-1 block font-mono text-[14px] font-semibold text-slate-800">{value}</b></div>
}
