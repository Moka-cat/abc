'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, BarChart2, RefreshCw, AlertCircle, TrendingUp, Activity } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { fetchRetrievalStats, fetchGraphStats, type RetrievalStats, type GraphStats } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

const PALETTE = [
  '#38bdf8', '#6366f1', '#2dd4bf', '#fb923c', '#a78bfa',
  '#34d399', '#f472b6', '#facc15', '#60a5fa', '#f87171',
]

const DIST_ORDER = ['0', '1-3', '4-7', '8-10', '10+']

export default function StatsPage() {
  const router = useRouter()
  const [stats,      setStats]      = useState<RetrievalStats | null>(null)
  const [graphStats, setGraphStats] = useState<GraphStats | null>(null)
  const [loading,    setLoading]    = useState(true)

  useEffect(() => { document.title = '检索统计 — PyroPlanner' }, [])

  const load = async () => {
    setLoading(true)
    const [data, gdata] = await Promise.all([fetchRetrievalStats(), fetchGraphStats()])
    setStats(data)
    setGraphStats(gdata)
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  // ── Derived data for charts ──────────────────────────────────────────────

  const distData = DIST_ORDER.map(k => ({
    name: k + ' 条',
    value: stats?.result_count_distribution[k] ?? 0,
  }))

  const topQueryData = (stats?.top_queries ?? []).slice(0, 10).map(q => ({
    name: q.query.length > 14 ? q.query.slice(0, 12) + '…' : q.query,
    fullName: q.query,
    次数: q.count,
  }))

  const nodeTypePieData = graphStats
    ? Object.entries(graphStats.node_types).sort((a, b) => b[1] - a[1]).map(([t, c]) => ({ name: t, value: c }))
    : []

  const edgeTypePieData = graphStats
    ? Object.entries(graphStats.edge_types).sort((a, b) => b[1] - a[1]).map(([t, c]) => ({ name: t, value: c }))
    : []

  const topConnectedData = graphStats?.top_connected.slice(0, 8).map(n => ({
    name: n.label.length > 10 ? n.label.slice(0, 8) + '…' : n.label,
    fullName: n.label,
    度数: n.degree,
  })) ?? []

  const nsData = stats
    ? Object.entries(stats.namespaces).sort((a, b) => b[1] - a[1]).map(([ns, c]) => ({ name: ns, 次数: c }))
    : []

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
          <BarChart2 size={14} className="text-brand" />
          <span className="font-semibold text-sm">检索统计</span>
        </div>
        <button onClick={load} title="刷新"
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all ml-1">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
        <NavTabs />
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2 text-ghost">
            <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">加载中…</span>
          </div>
        ) : (
          <div className="max-w-4xl mx-auto space-y-5 pb-6">

            {/* ── Summary cards ────────────────────────────────────────── */}
            {stats && (
            <div className="grid grid-cols-3 gap-3">
              {[
                { label: '总查询次数', value: stats.total_runs.toLocaleString(),              icon: <TrendingUp size={16} className="text-blue-400" />,   color: 'from-blue-400 to-blue-500' },
                { label: '平均结果数', value: stats.avg_result_count.toFixed(1),               icon: <BarChart2  size={16} className="text-teal-400" />,   color: 'from-teal-400 to-teal-500' },
                { label: '零结果查询', value: stats.zero_result_queries.length.toLocaleString(), icon: <AlertCircle size={16} className="text-orange-400" />, color: 'from-orange-400 to-orange-500' },
              ].map(({ label, value, icon, color }) => (
                <div key={label}
                  className="bg-surface border border-line rounded-xl px-5 py-4 flex items-center gap-4">
                  <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${color} flex items-center justify-center shrink-0 shadow-soft`}>
                    <span className="text-white">{icon}</span>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-ink leading-tight">{value}</p>
                    <p className="text-[10px] text-ghost mt-0.5">{label}</p>
                  </div>
                </div>
              ))}
            </div>
            )}

            {/* ── Knowledge graph summary cards ─────────────────────── */}
            {graphStats && (
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: '图谱节点总数', value: graphStats.node_count.toLocaleString(), icon: <Activity size={16} className="text-purple-400" />, color: 'from-purple-400 to-indigo-500' },
                { label: '图谱边总数',   value: graphStats.edge_count.toLocaleString(), icon: <Activity size={16} className="text-indigo-400" />, color: 'from-indigo-400 to-blue-500' },
              ].map(({ label, value, icon, color }) => (
                <div key={label}
                  className="bg-surface border border-line rounded-xl px-5 py-4 flex items-center gap-4">
                  <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${color} flex items-center justify-center shrink-0 shadow-soft`}>
                    <span className="text-white">{icon}</span>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-ink leading-tight">{value}</p>
                    <p className="text-[10px] text-ghost mt-0.5">{label}</p>
                  </div>
                </div>
              ))}
            </div>
            )}

            {/* ── Result count distribution bar chart ──────────────── */}
            {stats && (
            <div className="bg-surface border border-line rounded-xl px-5 py-4">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-4">
                检索结果数分布
              </h3>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={distData} barCategoryGap="35%">
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={28} />
                  <Tooltip
                    contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }}
                    cursor={{ fill: '#f1f5f9' }}
                  />
                  <Bar dataKey="value" name="查询次数" radius={[6, 6, 0, 0]}>
                    {distData.map((_, i) => (
                      <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            )}

            {/* ── Top queries bar chart ───────────────────────────── */}
            {topQueryData.length > 0 && (
            <div className="bg-surface border border-line rounded-xl px-5 py-4">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-4">
                高频查询 Top {topQueryData.length}
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={topQueryData} layout="vertical" barCategoryGap="30%">
                  <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={90} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }}
                    formatter={(v, _n, p) => [v, p.payload.fullName]}
                    cursor={{ fill: '#f1f5f9' }}
                  />
                  <Bar dataKey="次数" radius={[0, 6, 6, 0]} fill="#38bdf8" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            )}

            {/* ── Graph node type & edge type pie ─────────────────── */}
            {graphStats && (nodeTypePieData.length > 0 || edgeTypePieData.length > 0) && (
            <div className="grid grid-cols-2 gap-4">
              {nodeTypePieData.length > 0 && (
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-3">
                  节点类型分布
                </h3>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie
                      data={nodeTypePieData}
                      cx="50%" cy="45%"
                      innerRadius={42} outerRadius={70}
                      paddingAngle={3}
                      dataKey="value"
                    >
                      {nodeTypePieData.map((_, i) => (
                        <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                    <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              )}
              {edgeTypePieData.length > 0 && (
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-3">
                  关系类型分布
                </h3>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie
                      data={edgeTypePieData}
                      cx="50%" cy="45%"
                      innerRadius={42} outerRadius={70}
                      paddingAngle={3}
                      dataKey="value"
                    >
                      {edgeTypePieData.map((_, i) => (
                        <Cell key={i} fill={PALETTE[(i + 3) % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                    <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              )}
            </div>
            )}

            {/* ── Top connected nodes bar chart ─────────────────────── */}
            {topConnectedData.length > 0 && (
            <div className="bg-surface border border-line rounded-xl px-5 py-4">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-4">
                高连接度节点 Top {topConnectedData.length}
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={topConnectedData} layout="vertical" barCategoryGap="30%">
                  <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={80} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }}
                    formatter={(v, _n, p) => [v, p.payload.fullName]}
                    cursor={{ fill: '#f1f5f9' }}
                  />
                  <Bar dataKey="度数" radius={[0, 6, 6, 0]} fill="#6366f1" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            )}

            {/* ── Namespace breakdown ───────────────────────────────── */}
            {nsData.length > 1 && (
            <div className="bg-surface border border-line rounded-xl px-5 py-4">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-4">
                命名空间查询分布
              </h3>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={nsData} barCategoryGap="40%">
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={28} />
                  <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} cursor={{ fill: '#f1f5f9' }} />
                  <Bar dataKey="次数" radius={[6, 6, 0, 0]} fill="#2dd4bf" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            )}

            {/* ── Zero result queries ───────────────────────────────── */}
            {stats && stats.zero_result_queries.length > 0 && (
            <div className="bg-surface border border-line rounded-xl px-5 py-4">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-3">
                零结果查询 <span className="text-orange-400">（知识库缺口）</span>
              </h3>
              <div className="space-y-1.5">
                {stats.zero_result_queries.slice(0, 15).map((q, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <AlertCircle size={11} className="text-orange-300 shrink-0 mt-0.5" />
                    <span className="text-sm text-dim">{q}</span>
                  </div>
                ))}
                {stats.zero_result_queries.length > 15 && (
                  <p className="text-xs text-ghost pl-5">…还有 {stats.zero_result_queries.length - 15} 条</p>
                )}
              </div>
            </div>
            )}

            {!stats && !graphStats && (
              <div className="flex flex-col items-center gap-2 py-16 text-ghost">
                <BarChart2 size={32} className="text-blue-100" />
                <p className="text-sm">暂无统计数据</p>
              </div>
            )}

          </div>
        )}
      </div>
    </div>
  )
}
