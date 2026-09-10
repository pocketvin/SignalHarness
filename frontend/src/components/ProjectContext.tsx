import { useMemo, useState } from 'react'
import { ChevronDown, Layers3, SlidersHorizontal, Sparkles } from 'lucide-react'
import { t } from '../i18n'
import type { Lang, ProfilePayload } from '../types'

const IMPORTANCE = ['critical', 'important', 'normal', 'low', 'ignore'] as const

function importanceLabel(value: string, lang: Lang) {
  const zh: Record<string, string> = { critical: 'Critical', important: 'Important', normal: 'Normal', low: 'Low', ignore: 'Ignore' }
  const en = zh
  return (lang === 'zh' ? zh : en)[value] ?? value
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[10px] text-slate-600">{children}</span>
}

export function ProjectContext({
  lang,
  profile,
  loading,
  status,
  onSavePreference,
  onSaveNaturalPreference,
}: {
  lang: Lang
  profile: ProfilePayload | null
  loading: boolean
  status: string
  onSavePreference: (scope: string, key: string, importance: string) => Promise<void>
  onSaveNaturalPreference: (instruction: string) => Promise<boolean>
}) {
  const [instruction, setInstruction] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const current = profile?.effective_profile
  const preferences = profile?.preferences ?? []
  const groups = useMemo(() => [
    ['dependency', current?.dependencies ?? []],
    ['protocol', current?.protocols ?? []],
    ['provider', current?.providers ?? []],
    ['runtime', current?.runtimes ?? []],
    ['module', current?.critical_modules ?? []],
    ['ecosystem', current?.monitored_ecosystem ?? []],
  ] as const, [current])
  const ruleCount = groups.reduce((sum, [, items]) => sum + items.length, 0)

  function activePreference(scope: string, key: string) {
    return preferences.find((pref) => pref.active !== false && pref.scope_type === scope && pref.scope_key.toLowerCase() === key.toLowerCase())
  }

  async function applyNaturalPreference() {
    if (!instruction.trim()) return
    setSubmitting(true)
    const saved = await onSaveNaturalPreference(instruction)
    if (saved) setInstruction('')
    setSubmitting(false)
  }

  return (
    <section id="profilePanel" className="border-y border-slate-200 bg-white/70">
      <div className="mx-auto max-w-[1500px] px-5 py-6 sm:px-8 xl:px-10">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(360px,.85fr)] lg:items-start">
          <div>
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-900"><Layers3 className="h-4 w-4 text-blue-600" />{t(lang, 'projectContext')}</div>
                <p className="mt-1 text-[10px] text-slate-500">{t(lang, 'profileSub')}</p>
              </div>
              <span className="font-mono text-[9px] text-slate-400">{profile?.profile_revision_id ?? '—'}</span>
            </div>
            <p className="mt-4 max-w-4xl text-[15px] leading-6 text-slate-700">{loading ? '…' : current?.purpose || current?.goal || '—'}</p>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <div className="mb-2 text-[9px] font-semibold text-slate-400">{t(lang, 'stack')}</div>
                <div className="flex flex-wrap gap-1.5">{[...(current?.tech_stack ?? []), ...(current?.runtimes ?? [])].map((item) => <Chip key={item}>{item}</Chip>)}</div>
              </div>
              <div>
                <div className="mb-2 text-[9px] font-semibold text-slate-400">{t(lang, 'protocols')}</div>
                <div className="flex flex-wrap gap-1.5">{[...(current?.protocols ?? []), ...(current?.providers ?? [])].map((item) => <Chip key={item}>{item}</Chip>)}</div>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
            <div className="flex items-center gap-2 text-[10px] font-semibold text-slate-700"><Sparkles className="h-3.5 w-3.5 text-blue-600" />{lang === 'zh' ? '快速调整项目偏好' : 'Quick project preference'}</div>
            <div className="mt-3 flex gap-2">
              <label className="sr-only" htmlFor="preferenceInput">{t(lang, 'preferences')}</label>
              <input
                id="preferenceInput"
                className="h-10 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 text-[11px] text-slate-800 outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-3 focus:ring-blue-500/10"
                type="text"
                autoComplete="off"
                maxLength={1000}
                placeholder={t(lang, 'preferencePlaceholder')}
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') void applyNaturalPreference() }}
              />
              <button className="h-10 shrink-0 rounded-lg bg-slate-950 px-4 text-[10px] font-semibold text-white hover:bg-blue-700 disabled:opacity-50" type="button" disabled={submitting || !instruction.trim()} onClick={() => void applyNaturalPreference()}>{t(lang, 'apply')}</button>
            </div>
            <p className="mt-2 min-h-4 text-[9px] text-slate-500" role="status" aria-live="polite">{status === 'saved' ? (lang === 'zh' ? '已生成新的 ProfileRevision。' : 'Saved as a new ProfileRevision.') : status === '…' ? '…' : status}</p>
          </div>
        </div>

        <details id="profilePreferences" className="group mt-5 border-t border-slate-200 pt-1">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-3 text-[10px] font-semibold text-slate-600 hover:text-slate-900 [&::-webkit-details-marker]:hidden">
            <span className="flex items-center gap-2"><SlidersHorizontal className="h-3.5 w-3.5" />{t(lang, 'preferences')}<span className="font-normal text-slate-400">{ruleCount}</span></span>
            <ChevronDown className="h-4 w-4 text-slate-400 transition-transform group-open:rotate-180" />
          </summary>
          <div className="grid gap-2 pb-5 sm:grid-cols-2 xl:grid-cols-3">
            {groups.flatMap(([scope, items]) => items.map((key) => {
              const pref = activePreference(scope, key)
              const value = pref?.importance ?? 'normal'
              return (
                <label className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5" key={`${scope}-${key}`}>
                  <span className="min-w-0">
                    <b className="block truncate text-[10px] font-medium text-slate-700">{key}</b>
                    <small className="mt-0.5 block font-mono text-[8px] text-slate-400">{scope}</small>
                  </span>
                  <select className="h-8 rounded-md border border-slate-200 bg-slate-50 px-2 text-[9px] text-slate-700 outline-none focus:border-blue-500" value={value} onChange={(event) => void onSavePreference(scope, key, event.target.value)}>
                    {IMPORTANCE.map((importance) => <option value={importance} key={importance}>{importanceLabel(importance, lang)}</option>)}
                  </select>
                </label>
              )
            }))}
          </div>
        </details>
      </div>
    </section>
  )
}
