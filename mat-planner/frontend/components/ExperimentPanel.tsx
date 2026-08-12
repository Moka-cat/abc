'use client'
import { useEffect, useState } from 'react'
import { X, ShieldCheck, ShieldAlert, ShieldOff, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import { fetchExperiment, type Protocol } from '@/lib/api'

const SAFETY: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  approved:     { label: '安全', cls: 'text-emerald-400 border-emerald-800 bg-emerald-950/40', icon: <ShieldCheck size={11}/> },
  needs_review: { label: '待审', cls: 'text-amber-400  border-amber-800  bg-amber-950/40',  icon: <ShieldAlert size={11}/> },
  blocked:      { label: '阻止', cls: 'text-red-400    border-red-800    bg-red-950/40',    icon: <ShieldOff  size={11}/> },
}

function PropGrid({ props }: { props: Record<string, { value: number; unit: string }> }) {
  const entries = Object.entries(props)
  if (!entries.length) return null
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="bg-[#0d1117] rounded-lg px-2.5 py-2 border border-[#21262d]">
          <p className="text-[10px] text-[#6e7681] mb-0.5 truncate">{k.replace(/_/g, ' ')}</p>
          <p className="text-sm font-semibold text-[#e6edf3]">
            {typeof v.value === 'number' ? v.value.toFixed(2) : v.value}
            <span className="text-[10px] font-normal text-[#6e7681] ml-1">{v.unit}</span>
          </p>
        </div>
      ))}
    </div>
  )
}

function FormulationTable({ f }: { f: Record<string, { fraction: number; role: string }> }) {
  const rows = Object.entries(f)
  if (!rows.length) return null
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-[#6e7681] border-b border-[#21262d]">
          <th className="text-left py-1 font-medium">组分</th>
          <th className="text-left py-1 font-medium">角色</th>
          <th className="text-right py-1 font-medium">占比</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-[#21262d]/50 last:border-0">
            <td className="py-1.5 font-mono text-[#e6edf3] font-medium">{k}</td>
            <td className="py-1.5 text-[#8b949e]">{v.role}</td>
            <td className="py-1.5 text-right text-[#e6edf3]">{(v.fraction * 100).toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ProtocolCard({ proto, defaultOpen }: { proto: Protocol; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const safety = proto.safety_status ? SAFETY[proto.safety_status] : null

  return (
    <div className="rounded-xl border border-[#21262d] overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3
                   bg-[#161b22] hover:bg-[#1c2128] transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <span className="text-xs text-[#6e7681] font-mono">#{proto.rank + 1}</span>
          {safety && (
            <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${safety.cls}`}>
              {safety.icon}{safety.label}
            </span>
          )}
        </div>
        {open
          ? <ChevronUp  size={13} className="text-[#6e7681]" />
          : <ChevronDown size={13} className="text-[#6e7681]" />
        }
      </button>

      {open && (
        <div className="px-4 py-3 space-y-4 text-xs bg-[#0d1117]">
          {proto.rationale && (
            <p className="text-[#8b949e] leading-relaxed">{proto.rationale}</p>
          )}

          {Object.keys(proto.formulation).length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#c9d1d9] mb-2">配方组成</p>
              <FormulationTable f={proto.formulation} />
            </div>
          )}

          {Object.keys(proto.predicted_properties).length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#c9d1d9] mb-2">预测性能</p>
              <PropGrid props={proto.predicted_properties as Record<string, { value: number; unit: string }>} />
            </div>
          )}

          {proto.steps.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-[#c9d1d9] mb-2">实验步骤</p>
              <ol className="space-y-2">
                {proto.steps.map((s, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="shrink-0 w-5 h-5 rounded-full bg-[#21262d] flex items-center
                                     justify-center text-[9px] font-bold text-[#6e7681] mt-px">
                      {i + 1}
                    </span>
                    <div className="flex-1">
                      <p className="text-[#c9d1d9] leading-snug">{s.step}</p>
                      {(s.temperature || s.duration) && (
                        <p className="text-[#6e7681] text-[10px] mt-0.5">
                          {[s.temperature, s.duration].filter(Boolean).join(' · ')}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {proto.safety_summary && (
            <div className="rounded-lg bg-[#161b22] border border-[#21262d] px-3 py-2.5">
              <p className="text-[10px] text-[#6e7681] mb-1">安全说明</p>
              <p className="text-[#8b949e] leading-relaxed">{proto.safety_summary}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ExperimentPanel({ experimentId, onClose }: { experimentId: string; onClose: () => void }) {
  const [exp, setExp] = useState<Awaited<ReturnType<typeof fetchExperiment>>>(null)

  useEffect(() => {
    setExp(null)
    fetchExperiment(experimentId).then(setExp)
    const t = setInterval(async () => {
      const data = await fetchExperiment(experimentId)
      setExp(data)
      if (data?.status === 'completed' || data?.status === 'failed') clearInterval(t)
    }, 5000)
    return () => clearInterval(t)
  }, [experimentId])

  return (
    <aside className="flex flex-col h-full w-80 shrink-0 bg-[#0d1117] border-l border-[#21262d]">
      <div className="flex items-center justify-between px-4 py-3.5 border-b border-[#21262d]">
        <span className="text-xs font-semibold text-[#8b949e] tracking-wider uppercase">实验详情</span>
        <button onClick={onClose} className="text-[#6e7681] hover:text-[#c9d1d9] transition-colors">
          <X size={15} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {!exp ? (
          <div className="flex items-center justify-center py-12 text-[#6e7681]">
            <Loader2 size={16} className="animate-spin mr-2" />
            <span className="text-xs">加载中…</span>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="px-1">
              <p className="text-xs text-[#6e7681] mb-1.5">研究目标</p>
              <p className="text-sm text-[#e6edf3] leading-relaxed">{exp.goal}</p>
              <div className="flex items-center gap-2 mt-2.5">
                <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${
                  exp.status === 'completed' ? 'border-emerald-800 text-emerald-400 bg-emerald-950/40' :
                  exp.status === 'failed'    ? 'border-red-800    text-red-400    bg-red-950/40' :
                  exp.status === 'running'   ? 'border-amber-800  text-amber-400  bg-amber-950/40' :
                  'border-[#30363d] text-[#6e7681]'
                }`}>{exp.status}</span>
                {exp.protocol_count > 0 && (
                  <span className="text-[10px] text-[#6e7681]">{exp.protocol_count} 个候选方案</span>
                )}
              </div>
            </div>

            {exp.protocols.length > 0 ? (
              <div className="space-y-2">
                {exp.protocols.map((p, i) => (
                  <ProtocolCard key={p.protocol_id} proto={p} defaultOpen={i === 0} />
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center py-8 text-[#6e7681]">
                <Loader2 size={14} className="animate-spin mr-2" />
                <span className="text-xs">方案生成中…</span>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
