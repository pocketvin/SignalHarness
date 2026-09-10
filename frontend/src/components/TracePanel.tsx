import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Bot,
  Braces,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  Coins,
  FileJson2,
  ShieldCheck,
  Terminal,
  Wrench,
} from 'lucide-react'
import { t } from '../i18n'
import type { Lang, ReasoningItem, TraceStep } from '../types'
import { stageForTrace, traceTitle, traceTone } from '../utils'

const STAGES = ['collect', 'route', 'evidence', 'analysis', 'decision', 'narrative'] as const
const STAGE_LABELS = {
  zh: { collect: '采集', route: '路由', evidence: '证据', analysis: '分析', decision: '决策', narrative: '摘要' },
  en: { collect: 'Collect', route: 'Route', evidence: 'Evidence', analysis: 'Analyze', decision: 'Decide', narrative: 'Narrate' },
}

function statusText(trace: TraceStep, lang: Lang) {
  const tone = traceTone(trace)
  if (tone === 'running') return t(lang, 'running')
  if (tone === 'error') return t(lang, 'error')
  if (tone === 'skipped') return t(lang, 'skipped')
  if (tone === 'warn') return lang === 'zh' ? '降级' : 'Fallback'
  return t(lang, 'success')
}

function statusClasses(trace: TraceStep) {
  const tone = traceTone(trace)
  if (tone === 'running') return 'border-blue-400/35 bg-blue-400/8 text-blue-200'
  if (tone === 'error') return 'border-rose-400/35 bg-rose-400/8 text-rose-200'
  if (tone === 'warn') return 'border-amber-400/35 bg-amber-400/8 text-amber-100'
  if (tone === 'skipped') return 'border-slate-600/50 bg-slate-800/45 text-slate-400'
  return 'border-emerald-400/25 bg-emerald-400/7 text-emerald-100'
}

function dotClasses(trace: TraceStep) {
  const tone = traceTone(trace)
  if (tone === 'running') return 'bg-blue-400 shadow-[0_0_0_4px_rgba(96,165,250,.12)]'
  if (tone === 'error') return 'bg-rose-400'
  if (tone === 'warn') return 'bg-amber-400'
  if (tone === 'skipped') return 'bg-slate-600'
  return 'bg-emerald-400'
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg border border-slate-200/10 bg-white/5 px-2.5 py-2">
      <span className="shrink-0 text-slate-500">{icon}</span>
      <span className="min-w-0">
        <span className="block text-[10px] text-slate-500">{label}</span>
        <b className="block truncate font-mono text-[11px] font-medium text-slate-200">{value}</b>
      </span>
    </div>
  )
}

function ReasoningItemView({ item }: { item: ReasoningItem }) {
  return (
    <div className="border-l border-slate-700 pl-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[10px] text-slate-300">{item.event_id || item.title || 'event'}</span>
        {item.risk_level && <span className="trace-chip">{item.risk_level}</span>}
        {item.semantic_relevance !== undefined && <span className="trace-chip">rel {item.semantic_relevance}</span>}
        {item.confidence !== undefined && <span className="trace-chip">conf {item.confidence}</span>}
        {item.source_quality && <span className="trace-chip">{item.source_quality}</span>}
      </div>
      {item.summary && <p className="mt-1.5 text-[11px] leading-5 text-slate-300">{item.summary}</p>}
      {item.uncertainty && (
        <p className="mt-2 border-l-2 border-amber-400/60 pl-2 text-[10px] leading-4 text-amber-100/85">{item.uncertainty}</p>
      )}
      {!!item.affected_modules?.length && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.affected_modules.slice(0, 5).map((module) => <span className="trace-chip" key={module}>{module}</span>)}
        </div>
      )}
      {!!(item.recommended_actions_zh || item.recommended_actions)?.length && (
        <ul className="mt-2 space-y-1 text-[10px] leading-4 text-slate-300">
          {(item.recommended_actions_zh || item.recommended_actions || []).map((action) => (
            <li className="flex gap-2" key={action}><span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-slate-500" />{action}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TraceCard({
  trace,
  index,
  lang,
  open,
  onToggle,
}: {
  trace: TraceStep
  index: number
  lang: Lang
  open: boolean
  onToggle: (value: boolean) => void
}) {
  const reasoning = trace.metadata
  const isLlm = trace.step === 'llm_agent_call'
  const waiting = reasoning?.reasoning_state === 'waiting_for_model'
  const items = reasoning?.reasoning_items ?? []
  const tools = trace.tools_executed ?? []
  const permissions = trace.permission_checks ?? []
  const errors = [...(trace.tool_errors ?? []), ...(trace.blocked_tools ?? [])]
  const model = [trace.provider, trace.model].filter(Boolean).join(' · ')

  return (
    <details
      className={`group border-b border-slate-800/80 last:border-0 ${open ? 'bg-white/[0.025]' : ''}`}
      open={open}
      onToggle={(event) => onToggle(event.currentTarget.open)}
    >
      <summary className="grid min-h-14 cursor-pointer list-none grid-cols-[16px_minmax(0,1fr)_auto_auto_18px] items-center gap-2.5 px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 [&::-webkit-details-marker]:hidden">
        <span className={`h-2 w-2 rounded-full ${dotClasses(trace)}`} aria-hidden="true" />
        <span className="min-w-0">
          <b className="block truncate text-[12px] font-semibold text-slate-100">{traceTitle(trace, lang)}</b>
          <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-500">{trace.agent_name || trace.agent || trace.step}</span>
        </span>
        {model && <span className="hidden max-w-44 truncate font-mono text-[9px] text-slate-500 xl:block">{model}</span>}
        <span className={`rounded-md border px-1.5 py-1 text-[9px] font-semibold ${statusClasses(trace)}`}>{statusText(trace, lang)}</span>
        <ChevronDown className="h-3.5 w-3.5 text-slate-500 transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>

      <div className="space-y-4 px-9 pb-4 pt-1">
        {isLlm && (
          <section>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[10px] font-semibold text-slate-200"><Bot className="h-3.5 w-3.5 text-blue-300" />{t(lang, 'reasoning')}</div>
              <span className="text-[9px] text-slate-600">{t(lang, 'reasoningNote')}</span>
            </div>
            <p className={`mt-2 text-[11px] leading-5 ${waiting ? 'text-blue-200' : 'text-slate-300'}`}>
              {waiting ? t(lang, 'waitingReasoning') : reasoning?.reasoning_summary || (lang === 'zh' ? '该步骤没有公开的结构化判断摘要。' : 'No public structured judgment summary for this step.')}
            </p>
            {!!items.length && <div className="mt-3 space-y-3">{items.map((item, itemIndex) => <ReasoningItemView item={item} key={`${item.event_id ?? 'item'}-${itemIndex}`} />)}</div>}
          </section>
        )}

        <section className="border-t border-slate-800 pt-3">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold text-slate-400"><Terminal className="h-3.5 w-3.5" />{t(lang, 'technical')}</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            {trace.input_count !== null && trace.input_count !== undefined && <Metric icon={<Activity className="h-3 w-3" />} label={t(lang, 'input')} value={trace.input_count} />}
            {trace.output_count !== null && trace.output_count !== undefined && <Metric icon={<FileJson2 className="h-3 w-3" />} label={t(lang, 'output')} value={trace.output_count} />}
            {isLlm && <Metric icon={<Braces className="h-3 w-3" />} label={t(lang, 'schema')} value={trace.schema_valid === true ? 'valid' : trace.schema_valid === false ? 'invalid' : trace.output_schema || 'n/a'} />}
            {trace.duration_ms !== undefined && <Metric icon={<Clock3 className="h-3 w-3" />} label="Latency" value={`${trace.duration_ms} ms`} />}
            {trace.total_tokens !== null && trace.total_tokens !== undefined && <Metric icon={<Coins className="h-3 w-3" />} label={t(lang, 'tokens')} value={trace.total_tokens.toLocaleString()} />}
          </div>
          {!!tools.length && <div className="mt-3 flex flex-wrap gap-1.5">{tools.map((tool) => <span className="trace-chip" key={tool}><Wrench className="mr-1 inline h-2.5 w-2.5" />{tool}</span>)}</div>}
          {!!permissions.length && (
            <details className="mt-3 rounded-lg border border-slate-800 bg-black/10 px-3 py-2">
              <summary className="cursor-pointer list-none text-[10px] text-slate-400 [&::-webkit-details-marker]:hidden"><ShieldCheck className="mr-1.5 inline h-3 w-3" />{t(lang, 'permission')} · {permissions.length}</summary>
              <ul className="mt-2 space-y-1 font-mono text-[9px] leading-4 text-slate-500">{permissions.map((permission) => <li key={permission}>{permission}</li>)}</ul>
            </details>
          )}
          {!!errors.length && <div className="mt-3 space-y-1 border-l-2 border-rose-400/70 pl-2 text-[9px] leading-4 text-rose-200">{errors.map((error) => <p key={error}>{error}</p>)}</div>}
          {trace.error && <p className="mt-3 text-[9px] leading-4 text-rose-200"><CircleAlert className="mr-1 inline h-3 w-3" />{trace.error}</p>}
          {trace.detail && <p className="mt-3 text-[9px] leading-4 text-slate-500">{trace.detail}</p>}
        </section>
      </div>
    </details>
  )
}

export function TracePanel({ traces, eventCount, lang, compact = false }: { traces: TraceStep[]; eventCount: number; lang: Lang; compact?: boolean }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [touched, setTouched] = useState<Set<number>>(new Set())
  const [expandAll, setExpandAll] = useState(false)

  useEffect(() => {
    setExpanded((current) => new Set([...current].filter((index) => index < traces.length)))
    setTouched((current) => new Set([...current].filter((index) => index < traces.length)))
  }, [traces.length])

  const stageState = useMemo(() => {
    return STAGES.map((stage) => {
      const items = traces.filter((trace) => stageForTrace(trace) === stage)
      const tone = items.some((trace) => trace.status === 'running') ? 'running'
        : items.some((trace) => trace.status === 'error') ? 'error'
          : items.some((trace) => traceTone(trace) === 'warn') ? 'warn'
            : items.length ? 'success' : 'pending'
      return { stage, items, tone }
    })
  }, [traces])

  const latest = traces.at(-1)
  const llmCount = traces.filter((trace) => trace.step === 'llm_agent_call').length

  return (
    <div className={compact ? 'trace-console' : 'rounded-2xl border border-slate-200 bg-slate-950 shadow-[0_18px_60px_rgba(15,23,42,.12)]'}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div>
          <div className="flex items-center gap-2 text-[12px] font-semibold text-white"><Activity className="h-4 w-4 text-blue-400" />{t(lang, 'liveTrace')}</div>
          <p className="mt-1 text-[10px] text-slate-500">{t(lang, 'liveTraceSub')}</p>
        </div>
        <button
          className="rounded-md border border-slate-700 px-2.5 py-1.5 text-[10px] font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          type="button"
          aria-expanded={expandAll}
          onClick={() => {
            const next = !expandAll
            setExpandAll(next)
            setTouched(new Set(traces.map((_, index) => index)))
            setExpanded(next ? new Set(traces.map((_, index) => index)) : new Set())
          }}
        >
          {nextLabel(expandAll, lang)}
        </button>
      </div>

      <div className="grid grid-cols-6 border-b border-slate-800 px-3 py-2">
        {stageState.map(({ stage, tone }) => (
          <div className="flex min-w-0 items-center gap-1.5" key={stage}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone === 'success' ? 'bg-emerald-400' : tone === 'running' ? 'bg-blue-400' : tone === 'warn' ? 'bg-amber-400' : tone === 'error' ? 'bg-rose-400' : 'bg-slate-700'}`} />
            <span className={`truncate text-[9px] ${tone === 'pending' ? 'text-slate-700' : 'text-slate-400'}`}>{STAGE_LABELS[lang][stage]}</span>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between gap-3 border-b border-slate-800 bg-black/10 px-4 py-2 font-mono text-[9px] text-slate-500" role="status" aria-live="polite">
        <span className="truncate">{latest ? `${traceTitle(latest, lang)} · ${latest.status}` : t(lang, 'waitingTrace')}</span>
        <span className="shrink-0">{eventCount} SSE · {traces.length} Trace · {llmCount} LLM</span>
      </div>

      <div className={compact ? 'max-h-[520px] overflow-y-auto overscroll-contain' : 'max-h-[680px] overflow-y-auto overscroll-contain'}>
        {!traces.length ? (
          <div className="flex min-h-44 items-center justify-center px-6 text-center text-[11px] leading-5 text-slate-600">{t(lang, 'waitingTrace')}</div>
        ) : traces.map((trace, index) => {
          const autoOpen = trace.status === 'running' && trace.step === 'llm_agent_call'
          const open = expandAll || expanded.has(index) || (!touched.has(index) && autoOpen)
          return (
            <TraceCard
              key={`${index}-${trace.agent_name ?? trace.agent ?? trace.step}`}
              trace={trace}
              index={index}
              lang={lang}
              open={open}
              onToggle={(value) => {
                setTouched((current) => new Set(current).add(index))
                setExpanded((current) => {
                  const next = new Set(current)
                  if (value) next.add(index)
                  else next.delete(index)
                  return next
                })
                if (expandAll && !value) setExpandAll(false)
              }}
            />
          )
        })}
      </div>
    </div>
  )
}

function nextLabel(expandAll: boolean, lang: Lang) {
  return expandAll ? t(lang, 'collapseAll') : t(lang, 'expandAll')
}
