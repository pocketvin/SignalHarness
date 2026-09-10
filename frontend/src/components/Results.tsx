import { useMemo, useState } from 'react'
import {
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Filter,
  Layers2,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { t } from '../i18n'
import type { AllFilters, ChangeItem, Lang, ProductPayload, SourceTask, StreamRun } from '../types'
import { decisionLabel, decisionTone, formatDate, impactGroupLabel, IMPACT_GROUPS } from '../utils'

function DecisionBadge({ item, lang }: { item: ChangeItem; lang: Lang }) {
  const tone = decisionTone(item.decision)
  const classes = tone === 'alert'
    ? 'border-amber-300 bg-amber-50 text-amber-800'
    : tone === 'save'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
      : 'border-slate-200 bg-slate-50 text-slate-500'
  return <span className={`rounded-md border px-2 py-1 text-[9px] font-semibold ${classes}`}>{decisionLabel(item.decision, lang)}</span>
}

function Metric({ label, value, accent = false }: { label: string; value: React.ReactNode; accent?: boolean }) {
  return (
    <div className="border-l border-slate-200 pl-4 first:border-l-0 first:pl-0">
      <span className="block text-[9px] text-slate-400">{label}</span>
      <b className={`mt-1 block font-mono text-[20px] font-semibold tracking-tight ${accent ? 'text-blue-700' : 'text-slate-900'}`}>{value}</b>
    </div>
  )
}

function PriorityItem({ item, lang, onOpen }: { item: ChangeItem; lang: Lang; onOpen: (changeId: string) => void }) {
  return (
    <article className="group grid gap-4 border-t border-slate-200 py-5 first:border-t-0 md:grid-cols-[minmax(0,1fr)_96px]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[9px] font-semibold text-blue-700">{impactGroupLabel(item.impact_group, lang)}</span>
          <span className="text-slate-300">/</span>
          <span className="font-mono text-[8px] text-slate-400">{item.source_name}</span>
          <DecisionBadge item={item} lang={lang} />
        </div>
        <h3 className="mt-2 max-w-4xl text-[17px] font-semibold leading-6 tracking-[-.01em] text-slate-950">{item.summary_zh}</h3>
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div><span className="section-label">{t(lang, 'whatChanged')}</span><p className="mt-1.5 text-[11px] leading-5 text-slate-600">{item.what_changed_zh || item.summary_zh}</p></div>
          <div><span className="section-label">{t(lang, 'whyRelevant')}</span><p className="mt-1.5 text-[11px] leading-5 text-slate-600">{item.why_relevant_zh || item.project_impact_zh || '—'}</p></div>
        </div>
        {!!item.recommended_actions_zh?.length && (
          <div className="mt-4">
            <span className="section-label">{t(lang, 'suggestedActions')}</span>
            <ul className="mt-2 grid gap-1.5 text-[10px] leading-4 text-slate-600">
              {item.recommended_actions_zh.map((action) => <li className="flex gap-2" key={action}><span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-blue-500" />{action}</li>)}
            </ul>
          </div>
        )}
        <button className="mt-4 inline-flex items-center gap-1 text-[10px] font-semibold text-blue-700 hover:text-blue-900" type="button" onClick={() => onOpen(item.change_id)}>{t(lang, 'viewDetail')}<ArrowUpRight className="h-3.5 w-3.5" /></button>
      </div>
      <div className="flex items-start justify-between gap-3 md:block md:text-right">
        <div>
          <span className="font-mono text-[30px] font-semibold tracking-[-.05em] text-slate-950">{item.impact_score == null ? '—' : Number(item.impact_score).toFixed(1)}</span>
          <span className="ml-1 text-[8px] text-slate-400 md:block md:ml-0">{lang === 'zh' ? '影响分' : 'impact'}</span>
        </div>
        <span className="font-mono text-[8px] text-slate-400 md:mt-3 md:block">{formatDate(item.published_at, lang)}</span>
      </div>
    </article>
  )
}

export function Results({
  lang,
  product,
  productError,
  run,
  sourceTasks,
  legacySignals,
  onReloadChanges,
  onOpenChange,
}: {
  lang: Lang
  product: ProductPayload | null
  productError: string
  run: StreamRun | null
  sourceTasks: SourceTask[]
  legacySignals: Array<Record<string, unknown>>
  onReloadChanges: (filters: AllFilters, offset?: number) => Promise<void>
  onOpenChange: (changeId: string) => void
}) {
  const [filters, setFilters] = useState<AllFilters>({ query: '', impactGroup: 'all', analysis: 'all', sort: 'rank' })
  const report = product?.report
  const page = product?.all_changes
  const counts = report?.stats?.impact_group_counts ?? {}
  const grouped = useMemo(() => {
    const map = new Map<string, ChangeItem[]>()
    for (const item of page?.items ?? []) {
      const key = item.impact_group || 'other'
      const current = map.get(key) ?? []
      current.push(item)
      map.set(key, current)
    }
    return [...IMPACT_GROUPS.filter((key) => map.has(key)), ...[...map.keys()].filter((key) => !IMPACT_GROUPS.includes(key as (typeof IMPACT_GROUPS)[number]))]
      .map((key) => [key, map.get(key) ?? []] as const)
  }, [page?.items])

  if (!run && !product) {
    return (
      <section className="mx-auto max-w-[1500px] px-5 py-14 sm:px-8 xl:px-10">
        <div className="border-t border-slate-200 pt-8">
          <p className="max-w-xl text-[14px] leading-6 text-slate-400">{t(lang, 'reportEmpty')}</p>
        </div>
      </section>
    )
  }

  if (!product && productError) {
    return (
      <section className="mx-auto max-w-[1500px] px-5 py-10 sm:px-8 xl:px-10">
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-[11px] leading-5 text-amber-900">
          <CircleAlert className="mr-2 inline h-4 w-4" />Product projection unavailable: {productError}. {legacySignals.length ? `${legacySignals.length} raw signals remain available in the run audit.` : ''}
        </div>
      </section>
    )
  }

  return (
    <main id="results" className="mx-auto max-w-[1500px] px-5 py-10 sm:px-8 xl:px-10">
      <section className="grid gap-8 border-b border-slate-200 pb-9 lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,.75fr)]">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-semibold text-blue-700"><ShieldCheck className="h-4 w-4" />{t(lang, 'environmentReport')}</div>
          <p className="mt-3 max-w-4xl text-[18px] leading-8 tracking-[-.01em] text-slate-800">{report?.summary_zh || t(lang, 'reportEmpty')}</p>
          {!!report?.themes?.length && <div className="mt-4 flex flex-wrap gap-1.5">{report.themes.map((theme) => <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[8px] text-slate-500" key={theme}>{theme}</span>)}</div>}
        </div>
        <div className="grid grid-cols-2 gap-y-5 self-start sm:grid-cols-4 lg:grid-cols-2">
          <Metric label={t(lang, 'completeChanges')} value={report?.stats?.all_change_count ?? 0} />
          <Metric label={t(lang, 'deepAnalyzed')} value={report?.stats?.analyzed_count ?? 0} />
          <Metric label={t(lang, 'alerts')} value={report?.stats?.high_priority_count ?? 0} accent />
          <Metric label={t(lang, 'coverage')} value={report?.coverage_status ?? '—'} />
        </div>
      </section>

      <section className="grid gap-8 border-b border-slate-200 py-9 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div>
          <div className="flex items-end justify-between gap-4">
            <div><h2 className="text-[22px] font-semibold tracking-[-.025em] text-slate-950">{t(lang, 'priority')}</h2><p className="mt-1 text-[10px] text-slate-500">{t(lang, 'prioritySub')}</p></div>
            <span className="font-mono text-[9px] text-slate-400">{product?.top_changes.length ?? 0}</span>
          </div>
          <div className="mt-4">
            {!product?.top_changes.length ? <div className="border-t border-slate-200 py-8 text-[11px] text-slate-400">{t(lang, 'noPriority')}</div> : product.top_changes.map((item) => <PriorityItem item={item} lang={lang} onOpen={onOpenChange} key={item.change_id} />)}
          </div>
        </div>
        <aside className="border-l border-slate-200 pl-6 xl:sticky xl:top-20 xl:self-start">
          <div className="flex items-center gap-2 text-[10px] font-semibold text-slate-800"><Layers2 className="h-4 w-4 text-blue-600" />{t(lang, 'sourceHealth')}</div>
          <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
            {sourceTasks.length ? sourceTasks.map((source) => (
              <div className="flex items-start justify-between gap-3 py-2.5" key={`${source.source_name}-${source.source_type}`}>
                <span className="min-w-0 truncate text-[9px] text-slate-600">{source.source_name}</span>
                <span className={`shrink-0 font-mono text-[8px] ${source.status === 'success' ? 'text-emerald-700' : 'text-rose-600'}`}>{source.status} · {source.output_count ?? 0}</span>
              </div>
            )) : <div className="py-5 text-[10px] text-slate-400">—</div>}
          </div>
        </aside>
      </section>

      <section className="pt-9">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><h2 className="text-[22px] font-semibold tracking-[-.025em] text-slate-950">{t(lang, 'allRelevant')}</h2><p className="mt-1 text-[10px] text-slate-500">{t(lang, 'allRelevantSub')}</p></div>
          <span className="font-mono text-[9px] text-slate-400">{page ? `${page.count} / ${page.all_count}` : '0 / 0'}</span>
        </div>

        <div className="mt-5 flex flex-wrap gap-1.5">
          <button className={`filter-chip ${filters.impactGroup === 'all' ? 'active' : ''}`} type="button" onClick={() => { const next = { ...filters, impactGroup: 'all' }; setFilters(next); void onReloadChanges(next, 0) }}>{t(lang, 'board')}<span>{Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0)}</span></button>
          {IMPACT_GROUPS.map((group) => <button className={`filter-chip ${filters.impactGroup === group ? 'active' : ''}`} type="button" key={group} onClick={() => { const next = { ...filters, impactGroup: group }; setFilters(next); void onReloadChanges(next, 0) }}>{impactGroupLabel(group, lang)}<span>{Number(counts[group] ?? 0)}</span></button>)}
        </div>

        <form className="mt-4 grid gap-2 border-y border-slate-200 bg-white/65 p-3 sm:grid-cols-[minmax(220px,1fr)_150px_150px_auto]" onSubmit={(event) => { event.preventDefault(); void onReloadChanges(filters, 0) }}>
          <label className="relative block"><span className="sr-only">{t(lang, 'search')}</span><Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" /><input id="allQuery" className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-[10px] outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-3 focus:ring-blue-500/10" type="search" value={filters.query} placeholder={t(lang, 'search')} onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))} /></label>
          <label><span className="sr-only">{lang === 'zh' ? '分析状态' : 'Analysis status'}</span><select id="allAnalysis" className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[10px] outline-none focus:border-blue-500" value={filters.analysis} onChange={(event) => setFilters((current) => ({ ...current, analysis: event.target.value }))}><option value="all">{t(lang, 'analysisAll')}</option><option value="analyzed">{t(lang, 'analyzedOnly')}</option><option value="unanalyzed">{t(lang, 'unanalyzedOnly')}</option></select></label>
          <label><span className="sr-only">{lang === 'zh' ? '排序方式' : 'Sort order'}</span><select id="allSort" className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[10px] outline-none focus:border-blue-500" value={filters.sort} onChange={(event) => setFilters((current) => ({ ...current, sort: event.target.value }))}><option value="rank">{t(lang, 'sortRank')}</option><option value="score">{t(lang, 'sortScore')}</option><option value="newest">{t(lang, 'sortNewest')}</option></select></label>
          <button className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-slate-950 px-4 text-[10px] font-semibold text-white hover:bg-blue-700" type="submit"><Filter className="h-3.5 w-3.5" />{t(lang, 'apply')}</button>
        </form>

        <div className="mt-2">
          {grouped.map(([group, items]) => (
            <section className="border-b border-slate-200" key={group}>
              <div className="flex items-center justify-between gap-4 bg-slate-50/60 px-3 py-2.5"><span className="text-[10px] font-semibold text-slate-700">{impactGroupLabel(group, lang)}</span><span className="font-mono text-[8px] text-slate-400">{items.length}</span></div>
              {items.map((item) => (
                <button className="grid w-full grid-cols-[38px_minmax(0,1fr)_auto] items-center gap-3 border-t border-slate-100 px-3 py-3 text-left transition-colors hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 sm:grid-cols-[46px_minmax(0,1fr)_210px_auto]" type="button" key={item.change_id} onClick={() => onOpenChange(item.change_id)}>
                  <span className="font-mono text-[9px] text-slate-400">#{item.rank}</span>
                  <span className="min-w-0"><b className="block truncate text-[11px] font-medium text-slate-800">{item.summary_zh}</b><span className="mt-1 block truncate text-[9px] text-slate-400">{item.source_name} · {decisionLabel(item.decision, lang)}</span></span>
                  <span className="hidden truncate text-[9px] leading-4 text-slate-500 sm:block">{item.project_impact_zh || '—'}</span>
                  <span className="font-mono text-[10px] text-slate-500">{item.impact_score == null ? '—' : Number(item.impact_score).toFixed(1)}</span>
                </button>
              ))}
            </section>
          ))}
          {page && !page.items.length && <div className="border-b border-slate-200 py-8 text-center text-[10px] text-slate-400">—</div>}
        </div>

        {page && (
          <div className="mt-4 flex items-center justify-center gap-3">
            <button className="pagination-button" type="button" disabled={page.offset <= 0} onClick={() => void onReloadChanges(filters, Math.max(0, page.offset - page.limit))}><ChevronLeft className="h-3.5 w-3.5" />{t(lang, 'previous')}</button>
            <span className="font-mono text-[9px] text-slate-400">{page.count ? page.offset + 1 : 0}–{page.offset + page.returned} / {page.count}</span>
            <button className="pagination-button" type="button" disabled={!page.has_more} onClick={() => void onReloadChanges(filters, page.offset + page.limit)}>{t(lang, 'next')}<ChevronRight className="h-3.5 w-3.5" /></button>
          </div>
        )}
      </section>
    </main>
  )
}
