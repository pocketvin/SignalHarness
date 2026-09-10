import { useEffect, useState } from 'react'
import { ArrowUpRight, CheckCircle2, ExternalLink, MessageSquareText, X } from 'lucide-react'
import { t } from '../i18n'
import type { ChangeDetail, Lang } from '../types'
import { decisionLabel, formatDate, impactGroupLabel, safeExternalUrl } from '../utils'

function TriState({ label, value, onChange, lang }: { label: string; value: '' | 'true' | 'false'; onChange: (value: '' | 'true' | 'false') => void; lang: Lang }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[9px] font-medium text-slate-500">{label}</span>
      <select className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[10px] text-slate-700 outline-none focus:border-blue-500" value={value} onChange={(event) => onChange(event.target.value as '' | 'true' | 'false')}>
        <option value="">—</option><option value="true">{t(lang, 'yes')}</option><option value="false">{t(lang, 'no')}</option>
      </select>
    </label>
  )
}

export function ChangeDrawer({
  lang,
  detail,
  loading,
  status,
  onClose,
  onFeedback,
  onOutcome,
}: {
  lang: Lang
  detail: ChangeDetail | null
  loading: boolean
  status: string
  onClose: () => void
  onFeedback: (label: string, note: string) => Promise<void>
  onOutcome: (facts: Record<string, boolean | null>, note: string) => Promise<void>
}) {
  const [note, setNote] = useState('')
  const [impactObserved, setImpactObserved] = useState<'' | 'true' | 'false'>('')
  const [actionTaken, setActionTaken] = useState<'' | 'true' | 'false'>('')
  const [actionHelpful, setActionHelpful] = useState<'' | 'true' | 'false'>('')
  const [resolved, setResolved] = useState<'' | 'true' | 'false'>('')

  useEffect(() => {
    if (!detail && !loading) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = previous
      window.removeEventListener('keydown', onKey)
    }
  }, [detail, loading, onClose])

  useEffect(() => {
    setNote('')
    setImpactObserved('')
    setActionTaken('')
    setActionHelpful('')
    setResolved('')
  }, [detail?.change_id])

  if (!detail && !loading) return null

  const statusText = status === 'feedback-saved' ? (lang === 'zh' ? '反馈已保存' : 'Feedback saved')
    : status === 'outcome-saved' ? (lang === 'zh' ? '实际结果已保存' : 'Outcome saved')
      : status === 'missing-outcome' ? t(lang, 'outcome')
        : status

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/30 backdrop-blur-[2px]" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose() }}>
      <section id="changeDetail" className="absolute inset-y-0 right-0 w-full max-w-2xl overflow-y-auto overscroll-contain border-l border-slate-200 bg-surface shadow-[-24px_0_70px_rgba(15,23,42,.18)]" role="dialog" aria-modal="true" aria-labelledby="change-drawer-title">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-5 border-b border-slate-200 bg-surface/95 px-6 py-5 backdrop-blur sm:px-8">
          <div className="min-w-0">
            <div className="text-[9px] font-semibold text-blue-700">{detail ? impactGroupLabel(detail.impact_group, lang) : '—'}</div>
            <h2 id="change-drawer-title" className="mt-1 text-[20px] font-semibold leading-7 tracking-[-.02em] text-slate-950">{detail?.summary_zh || (loading ? 'Loading…' : '—')}</h2>
            {detail && <p className="mt-2 font-mono text-[8px] text-slate-400">{detail.change_id} · {detail.source_name} · {decisionLabel(detail.decision, lang)} · {formatDate(detail.published_at, lang)}</p>}
          </div>
          <button className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" type="button" aria-label={t(lang, 'close')} onClick={onClose}><X className="h-4 w-4" /></button>
        </header>

        {loading && !detail ? <div className="p-8 text-[11px] text-slate-400">Loading…</div> : detail && (
          <div className="space-y-0 px-6 pb-12 sm:px-8">
            <section className="drawer-section">
              <span className="section-label">{t(lang, 'whatChanged')}</span>
              <p>{detail.what_changed_zh || detail.summary_zh}</p>
            </section>
            <section className="drawer-section">
              <span className="section-label">{t(lang, 'whyRelevant')}</span>
              <p>{detail.why_relevant_zh || detail.project_impact_zh || '—'}</p>
            </section>
            {detail.before_after && (
              <section className="drawer-section"><span className="section-label">Before / After</span><p className="font-mono text-[10px]">{detail.before_after.before || '—'} → {detail.before_after.after || '—'}</p></section>
            )}
            <section className="drawer-section">
              <span className="section-label">{t(lang, 'affected')}</span>
              <div className="mt-2 flex flex-wrap gap-1.5">{detail.affected_modules?.length ? detail.affected_modules.map((module) => <span className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[9px] text-slate-600" key={module}>{module}</span>) : <span className="text-[10px] text-slate-400">—</span>}</div>
            </section>
            <section className="drawer-section">
              <span className="section-label">{t(lang, 'suggestedActions')}</span>
              <ol className="mt-3 space-y-2 text-[11px] leading-5 text-slate-650">
                {(detail.recommended_actions_zh || detail.recommended_actions || []).length ? (detail.recommended_actions_zh || detail.recommended_actions || []).map((action, index) => (
                  <li className="grid grid-cols-[22px_1fr] gap-2" key={action}><span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 font-mono text-[8px] text-white">{index + 1}</span><span>{action}</span></li>
                )) : <li>{t(lang, 'noAction')}</li>}
              </ol>
            </section>
            <section className="drawer-section">
              <span className="section-label">{t(lang, 'evidence')}</span>
              <div className="mt-2 space-y-2">
                {detail.evidence?.length ? detail.evidence.map((evidence, index) => {
                  const url = safeExternalUrl(evidence.url)
                  if (!url) return null
                  return <a className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-[10px] text-slate-600 hover:border-blue-300 hover:text-blue-800" href={url} target="_blank" rel="noopener noreferrer" key={`${url}-${index}`}><span className="min-w-0"><b className="block truncate font-medium">{evidence.source_name || detail.source_name}</b><span className="mt-0.5 block truncate font-mono text-[8px] text-slate-400">{evidence.source_quality || '—'} · {url}</span></span><ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0" /></a>
                }) : <span className="text-[10px] text-slate-400">—</span>}
              </div>
            </section>

            <section className="mt-5 rounded-xl border border-slate-200 bg-white p-5">
              <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-800"><MessageSquareText className="h-4 w-4 text-blue-600" />{t(lang, 'feedback')}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {[['useful', t(lang, 'useful')], ['not_useful', t(lang, 'notUseful')], ['false_positive', t(lang, 'falsePositive')], ['too_generic', t(lang, 'tooGeneric')]].map(([value, label]) => (
                  <button className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[9px] font-semibold text-slate-600 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-800" type="button" key={value} onClick={() => void onFeedback(value, note)}>{label}</button>
                ))}
              </div>
              <div className="mt-5 border-t border-slate-100 pt-4">
                <div className="mb-3 flex items-center gap-2 text-[10px] font-semibold text-slate-700"><CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />{t(lang, 'outcome')}</div>
                <div className="grid gap-3 sm:grid-cols-2"><TriState label={t(lang, 'impactObserved')} value={impactObserved} onChange={setImpactObserved} lang={lang} /><TriState label={t(lang, 'actionTaken')} value={actionTaken} onChange={setActionTaken} lang={lang} /><TriState label={t(lang, 'actionHelpful')} value={actionHelpful} onChange={setActionHelpful} lang={lang} /><TriState label={t(lang, 'resolved')} value={resolved} onChange={setResolved} lang={lang} /></div>
              </div>
              <label className="mt-4 block"><span className="sr-only">{t(lang, 'note')}</span><textarea className="min-h-20 w-full resize-y rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] leading-5 text-slate-700 outline-none placeholder:text-slate-400 focus:border-blue-500" maxLength={2000} placeholder={t(lang, 'note')} value={note} onChange={(event) => setNote(event.target.value)} /></label>
              <div className="mt-3 flex items-center gap-3"><button className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-slate-950 px-4 text-[10px] font-semibold text-white hover:bg-blue-700" type="button" onClick={() => void onOutcome({ impact_observed: tri(impactObserved), action_taken: tri(actionTaken), action_helpful: tri(actionHelpful), resolved: tri(resolved) }, note)}>{t(lang, 'saveOutcome')}<ArrowUpRight className="h-3.5 w-3.5" /></button><span className="text-[9px] text-slate-500" role="status" aria-live="polite">{statusText}</span></div>
            </section>
          </div>
        )}
      </section>
    </div>
  )
}

function tri(value: '' | 'true' | 'false'): boolean | null {
  if (value === '') return null
  return value === 'true'
}
