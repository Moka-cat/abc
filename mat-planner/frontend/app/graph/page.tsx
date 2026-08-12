'use client'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, GitFork, Search, RefreshCw, ChevronRight, ArrowRight, Loader2, FlaskConical, LayoutList, Share2 } from 'lucide-react'
import {
  fetchGraphStats, searchGraphNodes, fetchGraphNodes, fetchNeighbors, rebuildGraph, fetchGraphEdges,
  type GraphStats, type GraphNode, type GraphEdge,
} from '@/lib/api'
import NavTabs from '@/components/NavTabs'
import dynamic from 'next/dynamic'

// Dynamically import ForceGraph to avoid SSR issues with D3/canvas
const ForceGraph = dynamic(() => import('@/components/ForceGraph'), { ssr: false, loading: () => (
  <div className="flex items-center justify-center h-64 text-ghost gap-2">
    <Loader2 size={16} className="animate-spin text-blue-400" />
    <span className="text-sm">载入图谱渲染器…</span>
  </div>
) })

// Edge type colour palette
const EDGE_COLOR: Record<string, string> = {
  CO_OCCURS_WITH:     'bg-blue-100 text-blue-600',
  RELATED_TO:         'bg-purple-100 text-purple-600',
  IS_A:               'bg-green-100 text-green-600',
  PART_OF:            'bg-teal-100 text-teal-600',
  SYNTHESIZED_FROM:   'bg-orange-100 text-orange-600',
  TESTED_WITH:        'bg-yellow-100 text-yellow-600',
  MEASURED_BY:        'bg-red-100 text-red-600',
}
function edgeCls(edgeType: string) {
  return EDGE_COLOR[edgeType] ?? 'bg-gray-100 text-gray-600'
}

const NODE_TYPE_COLOR: Record<string, string> = {
  compound:   'bg-blue-50  text-blue-500  border-blue-200',
  property:   'bg-teal-50  text-teal-600  border-teal-200',
  concept:    'bg-purple-50 text-purple-600 border-purple-200',
  process:    'bg-orange-50 text-orange-600 border-orange-200',
}
function nodeTypeCls(t: string) {
  return NODE_TYPE_COLOR[t] ?? 'bg-gray-50 text-gray-500 border-gray-200'
}

// ── History breadcrumb ────────────────────────────────────────────────────────

function useHistory(initial: string | null) {
  const [stack, setStack] = useState<string[]>(initial ? [initial] : [])
  const push = (label: string) =>
    setStack(prev => prev[prev.length - 1] === label ? prev : [...prev, label])
  const pop = () => setStack(prev => prev.slice(0, -1))
  const reset = (label: string) => setStack([label])
  const current = stack[stack.length - 1] ?? null
  return { stack, current, push, pop, reset }
}

// ── Main page ─────────────────────────────────────────────────────────────────

function GraphContent() {
  const router       = useRouter()
  const searchParams = useSearchParams()
  const [stats, setStats]         = useState<GraphStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)
  const [query, setQuery]         = useState('')
  const [suggestions, setSuggestions] = useState<GraphNode[]>([])
  const [sugOpen, setSugOpen]     = useState(false)
  const [edges, setEdges]               = useState<GraphEdge[]>([])
  const [edgesLoading, setEdgesLoading] = useState(false)
  const [edgeTypeFilter, setEdgeTypeFilter] = useState<string>('')
  const [rebuilding, setRebuilding]     = useState(false)
  const [rebuildDone, setRebuildDone]   = useState(false)
  const [browseOpen, setBrowseOpen]     = useState(false)
  const [browseNodes, setBrowseNodes]   = useState<GraphNode[]>([])
  const [browsePage, setBrowsePage]     = useState(0)
  const [browseTypeFilter, setBrowseTypeFilter] = useState('')
  // Visual graph state
  const [viewMode, setViewMode] = useState<'list' | 'visual'>('list')
  const [visualNodes, setVisualNodes] = useState<GraphNode[]>([])
  const [visualEdges, setVisualEdges] = useState<GraphEdge[]>([])
  const [visualLoading, setVisualLoading] = useState(false)

  const PAGE_SIZE = 20
  const sugRef = useRef<HTMLDivElement>(null)
  const history = useHistory(null)

  useEffect(() => { document.title = '知识图谱 — PyroPlanner' }, [])

  // Load graph stats on mount; auto-explore if ?node= param present
  useEffect(() => {
    fetchGraphStats().then(s => { setStats(s); setStatsLoading(false) })
    const nodeParam = searchParams.get('node')
    if (nodeParam) explore(nodeParam)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Close suggestions on outside click
  useEffect(() => {
    if (!sugOpen) return
    const close = (e: MouseEvent) => {
      if (sugRef.current && !sugRef.current.contains(e.target as Node)) setSugOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [sugOpen])

  // Debounced search
  useEffect(() => {
    if (!query.trim()) { setSuggestions([]); return }
    const t = setTimeout(() => {
      searchGraphNodes(query, 10).then(nodes => {
        setSuggestions(nodes)
        setSugOpen(nodes.length > 0)
      })
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  const explore = useCallback(async (label: string) => {
    setSugOpen(false)
    setQuery(label)
    setEdgesLoading(true)
    setEdgeTypeFilter('')
    history.push(label)
    const result = await fetchNeighbors(label)
    setEdges(result)
    setEdgesLoading(false)
  }, [history])

  const loadBrowse = useCallback(async (page: number, nodeType: string) => {
    const nodes = await fetchGraphNodes({
      nodeType: nodeType || undefined,
      offset: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    })
    setBrowseNodes(nodes)
  }, [])

  useEffect(() => {
    if (browseOpen) loadBrowse(browsePage, browseTypeFilter)
  }, [browseOpen, browsePage, browseTypeFilter, loadBrowse])

  // Load visual graph data
  const loadVisual = useCallback(async () => {
    setVisualLoading(true)
    const [vNodes, vEdges] = await Promise.all([
      fetchGraphNodes({ limit: 200 }),
      fetchGraphEdges({ limit: 400 }),
    ])
    setVisualNodes(vNodes)
    setVisualEdges(vEdges)
    setVisualLoading(false)
  }, [])

  useEffect(() => {
    if (viewMode === 'visual' && visualNodes.length === 0) loadVisual()
  }, [viewMode, visualNodes.length, loadVisual])

  const handleRebuild = async () => {
    setRebuilding(true)
    setRebuildDone(false)
    const before = stats
    await rebuildGraph()
    let attempts = 0
    const poll = setInterval(async () => {
      attempts++
      const s = await fetchGraphStats()
      const changed = s && before && (s.node_count !== before.node_count || s.edge_count !== before.edge_count)
      if (changed || attempts >= 30) {
        clearInterval(poll)
        setStats(s)
        setRebuilding(false)
        setRebuildDone(true)
        // Reload visual if open
        if (viewMode === 'visual') loadVisual()
        setTimeout(() => setRebuildDone(false), 3000)
      }
    }, 2000)
  }

  // All unique edge types in current result
  const edgeTypes = Array.from(new Set(edges.map(e => e.edge_type))).sort()
  const visibleEdges = edgeTypeFilter ? edges.filter(e => e.edge_type === edgeTypeFilter) : edges
  const grouped = visibleEdges.reduce<Record<string, GraphEdge[]>>((acc, e) => {
    ;(acc[e.edge_type] ??= []).push(e)
    return acc
  }, {})

  const currentLabel = history.current

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
          <GitFork size={14} className="text-brand" />
          <span className="font-semibold text-sm">知识图谱</span>
        </div>
        {stats && (
          <span className="text-[10px] text-ghost ml-1">
            {stats.node_count.toLocaleString()} 节点 · {stats.edge_count.toLocaleString()} 边
          </span>
        )}
        <button
          onClick={handleRebuild}
          title="重建图谱"
          disabled={rebuilding}
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all ml-1 disabled:opacity-50"
        >
          <RefreshCw size={13} className={rebuilding ? 'animate-spin' : ''} />
        </button>
        {rebuildDone && <span className="text-[10px] text-green-500 font-medium ml-1">重建完成</span>}
        {rebuilding && <span className="text-[10px] text-ghost ml-1">重建中…</span>}

        {/* View mode toggle */}
        <div className="ml-auto flex items-center gap-1 bg-muted rounded-lg p-0.5 border border-line">
          <button
            onClick={() => setViewMode('list')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all
                        ${viewMode === 'list' ? 'bg-surface shadow-soft text-ink border border-line' : 'text-ghost hover:text-ink'}`}
          >
            <LayoutList size={12} /> 列表
          </button>
          <button
            onClick={() => setViewMode('visual')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all
                        ${viewMode === 'visual' ? 'bg-surface shadow-soft text-ink border border-line' : 'text-ghost hover:text-ink'}`}
          >
            <Share2 size={12} /> 可视化
          </button>
        </div>

        <NavTabs />
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-4xl mx-auto space-y-5 pb-6">

          {/* ── VISUAL MODE ─────────────────────────────────────────────── */}
          {viewMode === 'visual' && (
            <div className="bg-surface border border-line rounded-xl overflow-hidden">
              <div className="flex items-center justify-between px-5 py-3 border-b border-line">
                <h3 className="text-[10px] font-semibold text-ghost tracking-widest uppercase">
                  力导向图 — 前 200 节点 / 400 边
                </h3>
                <div className="flex items-center gap-3">
                  {/* Legend */}
                  {['compound', 'property', 'concept', 'process'].map(t => (
                    <span key={t} className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full shrink-0"
                        style={{ background: { compound:'#38bdf8', property:'#2dd4bf', concept:'#a78bfa', process:'#fb923c' }[t as 'compound'] ?? '#94a3b8' }} />
                      <span className="text-[9px] text-ghost">{t}</span>
                    </span>
                  ))}
                  <button onClick={loadVisual} title="刷新图数据"
                    disabled={visualLoading}
                    className="p-1 rounded-md text-ghost hover:text-ink hover:bg-muted transition-all disabled:opacity-40">
                    <RefreshCw size={11} className={visualLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>
              <div className="px-2 py-2">
                {visualLoading ? (
                  <div className="flex items-center justify-center h-64 gap-2 text-ghost">
                    <Loader2 size={16} className="animate-spin text-blue-400" />
                    <span className="text-sm">加载图数据…</span>
                  </div>
                ) : visualNodes.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-64 gap-2 text-ghost">
                    <GitFork size={32} className="text-blue-100" />
                    <p className="text-sm">图谱暂无数据，请先重建</p>
                  </div>
                ) : (
                  <ForceGraph
                    nodes={visualNodes}
                    edges={visualEdges}
                    focusLabel={currentLabel}
                    onNodeClick={n => explore(n.label)}
                    height={520}
                  />
                )}
              </div>
              <p className="text-[10px] text-ghost text-center pb-2">
                拖拽节点 · 滚轮缩放 · 点击节点探索邻居
              </p>
            </div>
          )}

          {/* ── LIST MODE stats cards ──────────────────────────────────── */}
          {viewMode === 'list' && !statsLoading && stats && (
            <div className="grid grid-cols-2 gap-3">
              {/* Node types */}
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <h3 className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
                  节点类型
                </h3>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(stats.node_types).sort((a, b) => b[1] - a[1]).map(([t, c]) => (
                    <span key={t}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg
                                  text-xs font-medium border ${nodeTypeCls(t)}`}>
                      {t}
                      <span className="font-bold">{c}</span>
                    </span>
                  ))}
                </div>
              </div>
              {/* Top connected */}
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <h3 className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
                  高连接度节点 Top 5
                </h3>
                <div className="space-y-1.5">
                  {stats.top_connected.slice(0, 5).map((n, i) => (
                    <button key={n.label}
                      onClick={() => explore(n.label)}
                      className="flex items-center gap-2 w-full text-left group">
                      <span className="text-[10px] text-ghost w-4 shrink-0 text-right">{i + 1}</span>
                      <span className="flex-1 text-xs text-ink truncate group-hover:text-blue-500 transition-colors">
                        {n.label}
                      </span>
                      <span className="text-[10px] text-blue-400 font-semibold shrink-0">{n.degree}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Browse all nodes (list mode) */}
          {viewMode === 'list' && (
          <div className="bg-surface border border-line rounded-xl overflow-hidden">
            <button
              onClick={() => setBrowseOpen(o => !o)}
              className="w-full flex items-center gap-2 px-5 py-3 hover:bg-muted transition-colors text-left"
            >
              <span className="text-[10px] font-semibold text-ghost tracking-widest uppercase flex-1">
                浏览所有节点
              </span>
              <ChevronRight size={13} className={`text-ghost transition-transform duration-200 ${browseOpen ? 'rotate-90' : ''}`} />
            </button>
            {browseOpen && (
              <div className="border-t border-line px-5 py-4 space-y-3">
                {stats && Object.keys(stats.node_types).length > 0 && (
                  <div className="flex gap-1.5 flex-wrap">
                    <button
                      onClick={() => { setBrowseTypeFilter(''); setBrowsePage(0) }}
                      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all
                                  ${!browseTypeFilter ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-500 hover:bg-blue-50'}`}
                    >
                      全部
                    </button>
                    {Object.keys(stats.node_types).sort().map(t => (
                      <button key={t}
                        onClick={() => { setBrowseTypeFilter(t === browseTypeFilter ? '' : t); setBrowsePage(0) }}
                        className={`px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all
                                    ${browseTypeFilter === t ? `${nodeTypeCls(t)}` : 'bg-gray-100 text-gray-500 hover:bg-blue-50'}`}
                      >
                        {t} ({stats.node_types[t]})
                      </button>
                    ))}
                  </div>
                )}
                <div className="space-y-1">
                  {browseNodes.length === 0 ? (
                    <p className="text-xs text-ghost text-center py-4">暂无节点数据</p>
                  ) : browseNodes.map(n => (
                    <button key={n.id}
                      onClick={() => explore(n.label)}
                      className="flex items-center gap-2 w-full text-left px-3 py-1.5 rounded-lg
                                 hover:bg-muted transition-colors group"
                    >
                      <span className={`text-[9px] px-1.5 py-0.5 rounded border font-semibold shrink-0 ${nodeTypeCls(n.node_type)}`}>
                        {n.node_type}
                      </span>
                      <span className="flex-1 text-xs text-ink truncate group-hover:text-blue-500 transition-colors">
                        {n.label}
                      </span>
                      <ArrowRight size={10} className="text-ghost group-hover:text-blue-400 shrink-0 opacity-0 group-hover:opacity-100 transition-all" />
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <button
                    disabled={browsePage === 0}
                    onClick={() => setBrowsePage(p => p - 1)}
                    className="px-2.5 py-1 rounded-lg text-xs border border-line text-ghost
                               hover:text-ink hover:border-blue-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                  >上一页</button>
                  <span className="text-[10px] text-ghost flex-1 text-center">第 {browsePage + 1} 页</span>
                  <button
                    disabled={browseNodes.length < PAGE_SIZE}
                    onClick={() => setBrowsePage(p => p + 1)}
                    className="px-2.5 py-1 rounded-lg text-xs border border-line text-ghost
                               hover:text-ink hover:border-blue-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                  >下一页</button>
                </div>
              </div>
            )}
          </div>
          )}

          {/* Search + explore (both modes) */}
          <div className="bg-surface border border-line rounded-xl px-5 py-4 space-y-4">
            <h3 className="text-[10px] font-semibold text-ghost tracking-widest uppercase">
              节点邻居探索
            </h3>

            <div className="relative" ref={sugRef}>
              <div className="flex items-center gap-2 bg-muted rounded-xl px-3 py-2
                              border border-line focus-within:border-blue-300 transition-colors">
                <Search size={13} className="text-ghost shrink-0" />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="输入节点名称搜索…"
                  className="bg-transparent text-sm text-ink placeholder-ghost outline-none flex-1"
                />
                {edgesLoading && <Loader2 size={13} className="text-blue-400 animate-spin shrink-0" />}
              </div>
              {sugOpen && suggestions.length > 0 && (
                <div className="absolute left-0 top-full mt-1 bg-surface border border-line
                                rounded-xl shadow-card z-50 w-full py-1 overflow-hidden">
                  {suggestions.map(n => (
                    <button key={n.id}
                      onClick={() => explore(n.label)}
                      className="flex items-center gap-2 w-full text-left px-3 py-2 hover:bg-muted transition-colors">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded border font-semibold ${nodeTypeCls(n.node_type)}`}>
                        {n.node_type}
                      </span>
                      <span className="text-xs text-ink flex-1 truncate">{n.label}</span>
                      <ChevronRight size={11} className="text-ghost shrink-0" />
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Breadcrumb */}
            {history.stack.length > 1 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                {history.stack.map((label, i) => (
                  <span key={i} className="flex items-center gap-1.5">
                    {i > 0 && <ChevronRight size={10} className="text-ghost" />}
                    <button
                      onClick={() => { if (i === history.stack.length - 1) return; history.reset(label); explore(label) }}
                      className={`text-xs transition-colors ${
                        i === history.stack.length - 1 ? 'text-blue-500 font-semibold cursor-default' : 'text-ghost hover:text-ink'
                      }`}
                    >{label}</button>
                  </span>
                ))}
              </div>
            )}

            {/* Edge type filter */}
            {currentLabel && !edgesLoading && edges.length > 0 && edgeTypes.length > 1 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <button
                  onClick={() => setEdgeTypeFilter('')}
                  className={`px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all
                              ${!edgeTypeFilter ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-500 hover:bg-blue-50 hover:text-blue-500'}`}
                >
                  全部 ({edges.length})
                </button>
                {edgeTypes.map(t => (
                  <button key={t}
                    onClick={() => setEdgeTypeFilter(t === edgeTypeFilter ? '' : t)}
                    className={`px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all
                                ${edgeTypeFilter === t ? edgeCls(t) : 'bg-gray-100 text-gray-500 hover:bg-blue-50 hover:text-blue-500'}`}
                  >
                    {t} ({edges.filter(e => e.edge_type === t).length})
                  </button>
                ))}
              </div>
            )}

            {/* Neighbor list */}
            {currentLabel && !edgesLoading && (
              <div>
                {edges.length === 0 ? (
                  <p className="text-sm text-ghost text-center py-6">该节点暂无连接</p>
                ) : (
                  <div className="space-y-4">
                    {Object.entries(grouped).sort((a, b) => b[1].length - a[1].length).map(([eType, grpEdges]) => (
                      <div key={eType}>
                        <div className="flex items-center gap-2 mb-2">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${edgeCls(eType)}`}>{eType}</span>
                          <span className="text-[10px] text-ghost">{grpEdges.length} 条</span>
                        </div>
                        <div className="space-y-1">
                          {grpEdges.map(e => {
                            const neighbor = e.source_label === currentLabel ? e.target_label : e.source_label
                            const outgoing = e.source_label === currentLabel
                            return (
                              <div key={e.id}
                                className="flex items-center gap-1 rounded-lg border border-transparent
                                           hover:border-blue-100 hover:bg-muted transition-all group">
                                <button
                                  onClick={() => explore(neighbor)}
                                  className="flex items-center gap-2 flex-1 text-left px-3 py-2 min-w-0"
                                >
                                  <span className="text-xs text-ghost shrink-0 w-3">{outgoing ? '→' : '←'}</span>
                                  <span className="flex-1 text-xs text-ink truncate group-hover:text-blue-500 transition-colors">{neighbor}</span>
                                  <span className="text-[10px] text-ghost shrink-0 font-mono">{e.weight.toFixed(2)}</span>
                                  <ArrowRight size={10} className="text-ghost group-hover:text-blue-400 shrink-0 transition-colors" />
                                </button>
                                <button
                                  onClick={() => router.push(`/entities?search=${encodeURIComponent(neighbor)}`)}
                                  title="在实体库中查看"
                                  className="p-1.5 mr-1.5 rounded-md text-ghost hover:text-teal-500
                                             hover:bg-teal-50 transition-all opacity-0 group-hover:opacity-100 shrink-0"
                                >
                                  <FlaskConical size={11} />
                                </button>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {!currentLabel && !edgesLoading && (
              <p className="text-xs text-ghost text-center py-4">
                搜索节点或点击上方高连接度节点开始探索
              </p>
            )}
          </div>

        </div>
      </div>
    </div>
  )
}

export default function GraphPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-screen gap-2 text-ghost">
        <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
        <span className="text-sm">加载中…</span>
      </div>
    }>
      <GraphContent />
    </Suspense>
  )
}
