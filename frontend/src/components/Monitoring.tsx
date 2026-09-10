import { useMemo, useState } from 'react'
import { Bell, CalendarClock, ChevronDown, Inbox, RefreshCw, Trash2 } from 'lucide-react'
import { t } from '../i18n'
import type { InboxPayload, Lang, ProviderOption, RunMode, ScheduleItem } from '../types'
import { formatDate } from '../utils'

const controlClass = 'h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-[10px] text-slate-700 outline-none focus:border-blue-500 focus:ring-3 focus:ring-blue-500/10'

export function Monitoring({
  lang,
  schedules,
  inbox,
  providers,
  status,
  onRefresh,
  onCreate,
  onDelete,
  onRead,
}: {
  lang: Lang
  schedules: ScheduleItem[]
  inbox: InboxPayload
  providers: ProviderOption[]
  status: string
  onRefresh: () => Promise<void>
  onCreate: (body: Record<string, unknown>) => Promise<void>
  onDelete: (scheduleId: string) => Promise<void>
  onRead: (inboxId: string) => Promise<void>
}) {
  const [cadence, setCadence] = useState('24h')
  const [localTime, setLocalTime] = useState('09:00')
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [mode, setMode] = useState<RunMode>('mock-agent')
  const [providerId, setProviderId] = useState('')
  const readyProviderId = useMemo(() => providerId || providers[0]?.id || '', [providerId, providers])

  async function create() {
    await onCreate({
      cadence,
      timezone,
      mode,
      max_events: 12,
      max_events_per_source: 4,
      ...(cadence === 'daily' ? { local_time: localTime } : {}),
      ...(mode === 'agent' && readyProviderId ? { provider_id: readyProviderId } : {}),
    })
  }

  return (
    <details id="continuousMonitoring" className="group border-y border-slate-200 bg-white/70">
      <summary className="mx-auto flex max-w-[1500px] cursor-pointer list-none items-center justify-between gap-4 px-5 py-5 sm:px-8 xl:px-10 [&::-webkit-details-marker]:hidden">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg bg-slate-950 text-white"><Bell className="h-4 w-4" /></span>
          <span>
            <b className="block text-[12px] text-slate-900">{t(lang, 'monitoring')}</b>
            <span className="mt-1 block text-[10px] text-slate-500">{t(lang, 'monitoringSub')}</span>
          </span>
        </div>
        <span className="flex items-center gap-3">
          <span className="font-mono text-[9px] text-slate-400">{schedules.filter((item) => item.enabled).length} {lang === 'zh' ? '个计划' : 'schedule'} · {inbox.items.filter((item) => !item.read_at).length} {lang === 'zh' ? '条未读' : 'unread'}</span>
          <ChevronDown className="h-4 w-4 text-slate-400 transition-transform group-open:rotate-180" />
        </span>
      </summary>

      <div className="border-t border-slate-200 bg-slate-50/70">
        <div className="mx-auto grid max-w-[1500px] gap-8 px-5 py-6 sm:px-8 lg:grid-cols-2 xl:px-10">
          <section>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-800"><CalendarClock className="h-4 w-4 text-blue-600" />{t(lang, 'schedule')}</div>
              <span className="font-mono text-[9px] text-slate-400">{schedules.length}</span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
              <select aria-label="Schedule cadence" className={controlClass} value={cadence} onChange={(event) => setCadence(event.target.value)}><option value="12h">12h</option><option value="24h">24h</option><option value="daily">{lang === 'zh' ? '每天' : 'Daily'}</option></select>
              {cadence === 'daily' && <input aria-label="Local time" className={controlClass} type="time" value={localTime} onChange={(event) => setLocalTime(event.target.value)} />}
              <input aria-label="Timezone" className={`${controlClass} min-w-0 ${cadence !== 'daily' ? 'sm:col-span-2' : ''}`} value={timezone} onChange={(event) => setTimezone(event.target.value)} />
              <select aria-label="Schedule analysis" className={controlClass} value={mode} onChange={(event) => setMode(event.target.value as RunMode)}><option value="mock-agent">{t(lang, 'mock')}</option><option value="demo">{t(lang, 'deterministic')}</option><option value="agent">{t(lang, 'real')}</option></select>
              {mode === 'agent' && <select aria-label="Provider" className={controlClass} value={readyProviderId} onChange={(event) => setProviderId(event.target.value)}>{providers.map((provider) => <option value={provider.id} key={provider.id}>{provider.label}</option>)}</select>}
              <button className="h-9 rounded-lg bg-slate-950 px-3 text-[10px] font-semibold text-white hover:bg-blue-700 sm:col-start-5" type="button" onClick={() => void create()}>{t(lang, 'createSchedule')}</button>
            </div>
            <div className="mt-4 divide-y divide-slate-200 border-y border-slate-200">
              {!schedules.length && <div className="py-5 text-[10px] text-slate-400">{t(lang, 'noSchedules')}</div>}
              {schedules.map((item) => (
                <div className="flex items-start justify-between gap-3 py-3" key={item.schedule_id}>
                  <div className="min-w-0">
                    <b className="text-[10px] font-semibold text-slate-700">{item.cadence === 'daily' ? `Daily ${item.local_time ?? ''}` : item.cadence} · {item.mode}</b>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[8px] text-slate-400"><span>next {formatDate(item.next_run_at, lang)}</span><span>checkpoint {formatDate(item.checkpoint_at, lang)}</span><span>{item.last_status ?? '—'}</span></div>
                  </div>
                  {item.enabled && <button className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600" type="button" aria-label={`${t(lang, 'disabled')} ${item.cadence}`} onClick={() => void onDelete(item.schedule_id)}><Trash2 className="h-3.5 w-3.5" /></button>}
                </div>
              ))}
            </div>
          </section>

          <section>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-800"><Inbox className="h-4 w-4 text-blue-600" />{t(lang, 'inbox')}</div>
              <button className="inline-flex items-center gap-1.5 text-[9px] font-semibold text-slate-500 hover:text-blue-700" type="button" onClick={() => void onRefresh()}><RefreshCw className="h-3.5 w-3.5" />{t(lang, 'refresh')}</button>
            </div>
            <div className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
              {!inbox.items.length && <div className="py-5 text-[10px] text-slate-400">{t(lang, 'noInbox')}</div>}
              {inbox.items.map((item) => (
                <div className={`py-3 ${!item.read_at ? 'border-l-2 border-blue-500 pl-3' : ''}`} key={item.inbox_id}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0"><b className="block text-[10px] leading-4 text-slate-700">{item.title}</b><span className="mt-1 block font-mono text-[8px] text-slate-400">{formatDate(item.created_at, lang)} · {item.category ?? '—'} · {item.impact_score?.toFixed(1) ?? '—'}</span></div>
                    {!item.read_at && <button className="shrink-0 text-[9px] font-semibold text-blue-700 hover:underline" type="button" onClick={() => void onRead(item.inbox_id)}>{lang === 'zh' ? '已读' : 'Mark read'}</button>}
                  </div>
                </div>
              ))}
            </div>
          </section>
          {!!status && <p className="text-[9px] text-slate-500" role="status" aria-live="polite">{status === 'saved' ? (lang === 'zh' ? '已保存' : 'Saved') : status}</p>}
        </div>
      </div>
    </details>
  )
}
