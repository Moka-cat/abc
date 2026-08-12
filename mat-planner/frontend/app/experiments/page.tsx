'use client'
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowLeft, FlaskConical, RefreshCw, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Loader2, Clock, AlertTriangle, BarChart2,
} from 'lucide-react'
import { fetchExperiments, fetchExperiment, type Experiment, type Protocol } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

const STATUS_MAP: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  completed: { label: '已完成', cls: 'bg-green-50 text-green-600 border-green-100',  icon: <CheckCircle2 size={10} /> },
  running:   { label: '运行中', cls: 'bg-blue-50 text-blue-500 border-blue-100',     icon: <Loader2 size={10} className="animate-spin" /> },
  pending:   { label: '等待中', cls: 'bg-gray-50 text-gray-400 border-gray-100',     icon: <Clock size={10} /> },
  failed:    { label: '失败',   cls: 'bg-red-50 text-red-400 border-red-100',        icon: <XCircle size={10} /> },
}

const SAFETY_MAP: Record<string, { label: string; cls: string }> = {
  approved:     { label: '✅ 安全',  cls: 'text-green-600 bg-green-50 border-green-100' },
  needs_review: { label: '⚠️ 待审',  cls: 'text-amber-600 bg-amber-50 border-amber-100' },
  blocked:      { label: '🚫 拦截',  cls: 'text-red-500 bg-red-50 border-red-100' },
}

function timeLabel(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_MAP[status] ?? { label: status, cls: 'bg-gray-50 text-gray-400 border-gray-100', icon: null }
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${s.cls}`}>
      {s.icon}{s.label}
    </span>
  )
}

function SafetyBadge({ status }: { status: string | null }) {
  if (!status) return null
  const s = SAFETY_MAP[status] ?? { label: status, cls: 'text-gray-400 bg-gray-50 border-gray-100' }
  return (
    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${s.cls}`}>
      {s.label}
    </span>
  )
}

function ProtocolCard({ proto }: { proto: Protocol }) {
  const [open, setOpen] = useState(false)

  const formEntries = Object.entries(proto.formulation)
  const perfEntries = Object.entries(proto.predicted_properties ?? {})

  return (
    <div className={`rounded-xl border overflow-hidden transition-all
                    ${proto.safety_status === 'blocked'
                      ? 'border-red-100 bg-red-50/30'
                      : proto.safety_status === 'needs_review'
                        ? 'border-amber-100 bg-amber-50/20'
                        : 'border-line bg-surface'
                    }`}>
      <div
        className="flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-muted/40 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0
                        text-xs font-bold text-white shadow-soft"
          style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}>
          {proto.rank + 1}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-ink">方案 {proto.rank + 1}</span>
            <SafetyBadge status={proto.safety_status} />
          </div>
          {/* Compact formulation preview */}
          <p className="text-[10px] text-ghost mt-0.5 truncate">
            {formEntries.map(([k, v]) => `${k} ${Math.round((v as {fraction:number}).fraction * 100)}%`).join(' · ')}
          </p>
        </div>
        <div className="text-ghost shrink-0">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </div>
      </div>

      {open && (
        <div className="border-t border-line px-4 py-3 space-y-4">
          {/* Formulation table */}
          <div>
            <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-2">配方组成</p>
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="py-1 pr-4 text-left text-ghost font-medium">组分</th>
                  <th className="py-1 px-2 text-right text-ghost font-medium">质量分数</th>
                  <th className="py-1 px-2 text-left text-ghost font-medium">作用</th>
                </tr>
              </thead>
              <tbody>
                {formEntries.map(([comp, info]) => {
                  const v = info as { fraction: number; role?: string }
                  return (
                    <tr key={comp} className="border-b border-line/50">
                      <td className="py-1.5 pr-4 font-medium text-ink">{comp}</td>
                      <td className="py-1.5 px-2 text-right text-blue-500 font-semibold">
                        {Math.round(v.fraction * 100)}%
                      </td>
                      <td className="py-1.5 px-2 text-ghost">{v.role ?? '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Predicted properties */}
          {perfEntries.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-2">预测性能</p>
              <div className="flex flex-wrap gap-3">
                {perfEntries.map(([prop, val]) => {
                  const v = val as { value: number; unit?: string; confidence?: string }
                  return (
                    <div key={prop} className="bg-muted rounded-lg px-3 py-1.5 text-center">
                      <p className="text-[9px] text-ghost">{prop}</p>
                      <p className="text-sm font-bold text-ink">{v.value}</p>
                      <p className="text-[9px] text-ghost">{v.unit ?? ''}</p>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Safety summary */}
          {proto.safety_summary && (
            <div className={`px-3 py-2 rounded-lg text-xs
                            ${proto.safety_status === 'blocked'
                              ? 'bg-red-50 text-red-600'
                              : proto.safety_status === 'needs_review'
                                ? 'bg-amber-50 text-amber-700'
                                : 'bg-green-50 text-green-700'
                            }`}>
              {proto.safety_summary}
            </div>
          )}

          {/* Steps */}
          {proto.steps && proto.steps.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-2">实验步骤</p>
              <ol className="space-y-1.5">
                {proto.steps.map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs">
                    <span className="w-5 h-5 rounded-full bg-blue-100 text-blue-500 flex items-center justify-center
                                     font-semibold text-[9px] shrink-0 mt-0.5">{i + 1}</span>
                    <div className="flex-1">
                      <span className="text-ink">{s.step}</span>
                      {(s.duration || s.temperature) && (
                        <span className="text-ghost ml-2 text-[10px]">
                          {[s.duration, s.temperature].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Instruments */}
          {proto.required_instruments && proto.required_instruments.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-1">所需仪器</p>
              <div className="flex flex-wrap gap-1.5">
                {proto.required_instruments.map(inst => (
                  <span key={inst} className="px-2 py-0.5 bg-muted rounded-md text-[10px] text-dim border border-line">
                    {inst}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ExperimentRow({ exp, onSelect }: { exp: Experiment; onSelect: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [protocols, setProtocols] = useState<Protocol[] | null>(null)
  const [loading, setLoading] = useState(false)

  const toggle = async () => {
    setExpanded(o => !o)
    if (!expanded && protocols === null) {
      setLoading(true)
      const detail = await fetchExperiment(exp.experiment_id)
      setProtocols(detail?.protocols ?? [])
      setLoading(false)
    }
  }

  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden hover:border-blue-200 transition-all">
      <div className="flex items-start gap-3 px-4 py-3 cursor-pointer" onClick={toggle}>
        <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 shadow-soft"
          style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}>
          <FlaskConical size={15} className="text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-ink truncate max-w-md">{exp.goal}</span>
            <StatusBadge status={exp.status} />
            <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-muted text-ghost border border-line">
              {exp.namespace}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-ghost">
            <span>{exp.protocol_count} 个方案</span>
            <span>{timeLabel(exp.created_at)}</span>
            {exp.error_message && (
              <span className="text-red-400 flex items-center gap-1">
                <AlertTriangle size={9} />
                {exp.error_message.slice(0, 40)}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={e => { e.stopPropagation(); onSelect(exp.experiment_id) }}
            title="在对话中查看"
            className="p-1.5 rounded-lg text-ghost hover:text-blue-500 hover:bg-blue-50 transition-all border border-transparent hover:border-blue-100"
          >
            <BarChart2 size={12} />
          </button>
          <div className="text-ghost">
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </div>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-line px-4 py-3 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-ghost py-4 justify-center">
              <Loader2 size={14} className="animate-spin text-blue-400" />
              <span className="text-sm">加载方案…</span>
            </div>
          ) : protocols && protocols.length > 0 ? (
            protocols.map(p => <ProtocolCard key={p.protocol_id} proto={p} />)
          ) : (
            <p className="text-xs text-ghost text-center py-4">暂无候选方案</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function ExperimentsPage() {
  const router = useRouter()
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => { document.title = '实验历史 — PyroPlanner' }, [])

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchExperiments(50)
    setExperiments(data)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const statusCounts = experiments.reduce<Record<string, number>>((acc, e) => {
    acc[e.status] = (acc[e.status] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="flex flex-col h-screen bg-app text-ink">
      <header
        className="flex items-center gap-3 px-6 py-3.5 border-b border-line
                   bg-surface/80 backdrop-blur-md shrink-0"
        style={{ boxShadow: '0 1px 0 0 #e2eaf7' }}
      >
        <button onClick={() => router.push('/')}
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all">
          <ArrowLeft size={15} />
        </button>
        <div className="flex items-center gap-2">
          <FlaskConical size={14} className="text-brand" />
          <span className="font-semibold text-sm">实验历史</span>
        </div>
        <span className="text-[10px] text-ghost ml-1">{experiments.length} 条记录</span>
        {Object.entries(statusCounts).map(([s, c]) => {
          const m = STATUS_MAP[s]
          return m ? (
            <span key={s} className={`px-1.5 py-0.5 rounded-full text-[9px] font-semibold border ${m.cls}`}>
              {c} {m.label}
            </span>
          ) : null
        })}
        <button onClick={load} title="刷新"
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all ml-1">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
        <NavTabs />
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2 text-ghost">
            <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">加载中…</span>
          </div>
        ) : experiments.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-20 text-ghost">
            <FlaskConical size={40} className="text-blue-100" />
            <p className="text-sm">暂无实验记录</p>
            <p className="text-xs text-ghost">在对话页面发起实验设计请求后，历史记录会出现在这里</p>
            <button
              onClick={() => router.push('/')}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium
                         bg-blue-50 border border-blue-200 text-blue-500 hover:bg-blue-100 transition-all mt-2"
            >
              前往对话页
            </button>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto space-y-3 pb-6">
            {experiments.map(exp => (
              <ExperimentRow
                key={exp.experiment_id}
                exp={exp}
                onSelect={() => router.push('/')}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
