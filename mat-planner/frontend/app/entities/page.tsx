'use client'
import { Suspense, useEffect, useState, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, Search, FlaskConical, ChevronDown, ChevronRight, GitFork, BarChart2, X, Check } from 'lucide-react'
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip, Legend,
  BarChart, Bar, XAxis, YAxis, Cell,
} from 'recharts'
import { fetchEntities, fetchDocuments, type EntityCard, type EntityProperty } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

// Key properties to show in the compact card view (in order)
const FEATURED_PROPS = [
  'density', 'detonation_velocity', 'detonation_pressure',
  'burning_rate', 'melting_point', 'decomposition_temperature',
  'specific_impulse', 'oxygen_balance', 'sensitivity',
]

const PROP_LABEL: Record<string, string> = {
  density:                   '密度',
  detonation_velocity:       '爆速',
  detonation_pressure:       '爆压',
  burning_rate:              '燃速',
  melting_point:             '熔点',
  decomposition_temperature: '分解温度',
  specific_impulse:          '比冲',
  oxygen_balance:            '氧平衡',
  sensitivity:               '感度',
  molecular_weight:          '分子量',
}

const COMPARE_COLORS = ['#38bdf8', '#6366f1', '#2dd4bf', '#fb923c', '#a78bfa']

function propLabel(name: string) {
  return PROP_LABEL[name] ?? name
}

function formatValue(p: EntityProperty) {
  if (p.value_numeric !== null && p.value_numeric !== undefined) {
    return `${p.value_numeric}${p.unit ? ' ' + p.unit : ''}`
  }
  return p.value_text ?? '—'
}

function formatCondition(c: Record<string, unknown> | null): string | null {
  if (!c || Object.keys(c).length === 0) return null
  const parts: string[] = []
  if (c.temperature != null) parts.push(`${c.temperature}${c.temperature_unit ?? '°C'}`)
  if (c.pressure    != null) parts.push(`${c.pressure}${c.pressure_unit ?? ' MPa'}`)
  if (c.atmosphere  != null) parts.push(String(c.atmosphere))
  if (c.solvent     != null) parts.push(String(c.solvent))
  if (parts.length === 0) {
    for (const [k, v] of Object.entries(c).slice(0, 3)) {
      parts.push(`${k}=${v}`)
    }
  }
  return parts.join(', ')
}

function entityTypeLabel(t: string) {
  const map: Record<string, string> = {
    explosive:   '炸药',
    propellant:  '推进剂',
    oxidizer:    '氧化剂',
    binder:      '粘合剂',
    fuel:        '燃料',
    formulation: '配方',
    compound:    '化合物',
  }
  return map[t] ?? t
}

function entityTypeColor(t: string) {
  const map: Record<string, string> = {
    explosive:   'bg-red-50 text-red-500 border-red-100',
    oxidizer:    'bg-orange-50 text-orange-500 border-orange-100',
    binder:      'bg-teal-50 text-teal-600 border-teal-100',
    fuel:        'bg-amber-50 text-amber-600 border-amber-100',
    formulation: 'bg-violet-50 text-violet-500 border-violet-100',
    propellant:  'bg-blue-50 text-blue-500 border-blue-100',
  }
  return map[t] ?? 'bg-gray-50 text-gray-500 border-gray-100'
}

// ── Compare Panel ─────────────────────────────────────────────────────────────

function getNumericProp(card: EntityCard, propKey: string): number | null {
  const p = card.properties.find(p => p.property === propKey)
  return p?.value_numeric ?? null
}

function ComparePanel({ cards, onClose }: { cards: EntityCard[]; onClose: () => void }) {
  // Build radar data: find all numeric props shared by at least one entity
  const allNumericProps = Array.from(new Set(
    cards.flatMap(c => c.properties.filter(p => p.value_numeric != null).map(p => p.property))
  ))

  // For radar we need normalized values per prop (0-100 scale)
  const radarProps = allNumericProps.filter(pk => {
    const vals = cards.map(c => getNumericProp(c, pk)).filter((v): v is number => v !== null)
    return vals.length >= 1
  }).slice(0, 8) // max 8 axes

  const radarData = radarProps.map(pk => {
    const vals = cards.map(c => getNumericProp(c, pk)).filter((v): v is number => v !== null)
    const max = Math.max(...vals)
    const row: Record<string, number | string> = { prop: propLabel(pk) }
    cards.forEach((c, i) => {
      const v = getNumericProp(c, pk)
      row[`entity_${i}`] = v != null && max > 0 ? Math.round((v / max) * 100) : 0
    })
    return row
  })

  // Bar chart: numeric props side by side (absolute values)
  const barProps = allNumericProps.slice(0, 6)
  const barData = barProps.map(pk => {
    const row: Record<string, number | string> = { prop: propLabel(pk) }
    cards.forEach((c, i) => {
      const v = getNumericProp(c, pk)
      if (v != null) row[`entity_${i}`] = v
    })
    return row
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/30 backdrop-blur-sm p-4">
      <div className="bg-surface w-full max-w-3xl max-h-[85vh] rounded-2xl shadow-2xl border border-line overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-line shrink-0">
          <BarChart2 size={15} className="text-brand" />
          <span className="font-semibold text-sm flex-1">属性对比</span>
          <div className="flex items-center gap-2 flex-wrap">
            {cards.map((c, i) => (
              <span key={c.id} className="flex items-center gap-1 text-xs font-medium"
                style={{ color: COMPARE_COLORS[i] }}>
                <span className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: COMPARE_COLORS[i] }} />
                {c.canonical_name}
              </span>
            ))}
          </div>
          <button onClick={onClose}
            className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all shrink-0">
            <X size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
          {/* Radar chart */}
          {radarData.length >= 3 ? (
            <div>
              <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
                综合性能雷达图（归一化至 0–100）
              </p>
              <ResponsiveContainer width="100%" height={280}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#e2e8f0" />
                  <PolarAngleAxis dataKey="prop" tick={{ fontSize: 11, fill: '#64748b' }} />
                  <PolarRadiusAxis angle={90} domain={[0, 100]} tick={{ fontSize: 9, fill: '#94a3b8' }} tickCount={4} />
                  {cards.map((c, i) => (
                    <Radar
                      key={c.id}
                      name={c.canonical_name}
                      dataKey={`entity_${i}`}
                      stroke={COMPARE_COLORS[i]}
                      fill={COMPARE_COLORS[i]}
                      fillOpacity={0.12}
                      strokeWidth={2}
                    />
                  ))}
                  <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                  <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          ) : radarData.length > 0 ? (
            <div>
              <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
                属性对比柱状图（绝对值）
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={barData} barCategoryGap="25%">
                  <XAxis dataKey="prop" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={32} />
                  <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                  {cards.map((c, i) => (
                    <Bar key={c.id} dataKey={`entity_${i}`} name={c.canonical_name}
                      fill={COMPARE_COLORS[i]} radius={[4, 4, 0, 0]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="text-sm text-ghost text-center py-8">所选实体无共同数值属性，无法对比</p>
          )}

          {/* Property table */}
          <div>
            <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
              详细属性对照表
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    <th className="py-2 pr-4 text-left text-ghost font-medium w-32">属性</th>
                    {cards.map((c, i) => (
                      <th key={c.id} className="py-2 px-3 text-left font-semibold"
                        style={{ color: COMPARE_COLORS[i] }}>{c.canonical_name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {allNumericProps.map(pk => (
                    <tr key={pk} className="border-b border-line/50 hover:bg-muted transition-colors">
                      <td className="py-2 pr-4 text-ghost font-medium">{propLabel(pk)}</td>
                      {cards.map(c => {
                        const p = c.properties.find(p => p.property === pk)
                        return (
                          <td key={c.id} className="py-2 px-3 text-ink">
                            {p ? formatValue(p) : <span className="text-ghost">—</span>}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                  {/* text props */}
                  {Array.from(new Set(
                    cards.flatMap(c => c.properties.filter(p => p.value_numeric == null).map(p => p.property))
                  )).map(pk => (
                    <tr key={pk} className="border-b border-line/50 hover:bg-muted transition-colors">
                      <td className="py-2 pr-4 text-ghost font-medium">{propLabel(pk)}</td>
                      {cards.map(c => {
                        const p = c.properties.find(p => p.property === pk)
                        return (
                          <td key={c.id} className="py-2 px-3 text-ink text-[11px] max-w-[140px] truncate">
                            {p ? formatValue(p) : <span className="text-ghost">—</span>}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Entity Card ───────────────────────────────────────────────────────────────

function EntityCardView({
  card, onViewGraph, selected, onToggleSelect,
}: {
  card: EntityCard
  onViewGraph: (name: string) => void
  selected: boolean
  onToggleSelect: (card: EntityCard) => void
}) {
  const [expanded, setExpanded] = useState(false)

  const featured = FEATURED_PROPS
    .map(k => card.properties.find(p => p.property === k))
    .filter(Boolean) as EntityProperty[]

  const rest = card.properties.filter(p => !FEATURED_PROPS.includes(p.property))

  return (
    <div className={`bg-surface border rounded-xl overflow-hidden transition-all duration-200
                    ${selected ? 'border-blue-300 shadow-soft ring-1 ring-blue-200' : 'border-line hover:border-blue-200 hover:shadow-soft'}`}>
      {/* Card header */}
      <div
        className="flex items-start gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded(e => !e)}
      >
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0
                     text-sm font-bold text-white shadow-soft"
          style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
        >
          {card.canonical_name.slice(0, 2).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-ink">{card.canonical_name}</span>
            <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${entityTypeColor(card.entity_type)}`}>
              {entityTypeLabel(card.entity_type)}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {card.aliases.length > 0 && (
              <p className="text-[10px] text-ghost truncate">
                别名：{card.aliases.slice(0, 4).join(' · ')}
              </p>
            )}
            {card.properties.length > 0 && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-ghost border border-line shrink-0">
                {card.properties.length} 个属性
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
          {/* Select for compare */}
          <button
            onClick={e => { e.stopPropagation(); onToggleSelect(card) }}
            title={selected ? '取消对比' : '加入对比'}
            className={`p-1 rounded-md border transition-all
                        ${selected
                          ? 'bg-blue-50 border-blue-200 text-blue-500'
                          : 'border-transparent text-ghost hover:text-blue-400 hover:bg-blue-50 hover:border-blue-100'
                        }`}
          >
            {selected ? <Check size={11} /> : <BarChart2 size={11} />}
          </button>
          <button
            onClick={e => { e.stopPropagation(); onViewGraph(card.canonical_name) }}
            title="在图谱中查看"
            className="p-1 rounded-md text-ghost hover:text-blue-500 hover:bg-blue-50
                       border border-transparent hover:border-blue-100 transition-all"
          >
            <GitFork size={11} />
          </button>
          <div className="text-ghost">
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </div>
        </div>
      </div>

      {/* Featured properties */}
      {featured.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {featured.map(p => {
            const cond = formatCondition(p.condition)
            return (
              <div key={p.property} className="flex items-baseline gap-1">
                <span className="text-[10px] text-ghost">{propLabel(p.property)}</span>
                <span className="text-xs font-medium text-ink">{formatValue(p)}</span>
                {cond && <span className="text-[9px] text-ghost opacity-70">@{cond}</span>}
              </div>
            )
          })}
        </div>
      )}

      {/* Expanded: remaining properties */}
      {expanded && rest.length > 0 && (
        <div className="border-t border-line px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-1.5">
          {rest.map(p => {
            const cond = formatCondition(p.condition)
            return (
              <div key={p.property} className="flex items-baseline gap-1 min-w-0">
                <span className="text-[10px] text-ghost shrink-0">{propLabel(p.property)}</span>
                <span className="text-xs font-medium text-ink truncate">{formatValue(p)}</span>
                {cond && <span className="text-[9px] text-ghost opacity-70 shrink-0">@{cond}</span>}
              </div>
            )
          })}
        </div>
      )}

      {card.properties.length === 0 && (
        <p className="px-4 pb-3 text-xs text-ghost">暂无属性数据</p>
      )}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

function EntitiesContent() {
  const router       = useRouter()
  const searchParams = useSearchParams()
  const [entities,   setEntities]   = useState<EntityCard[]>([])
  const [loading,    setLoading]    = useState(true)
  const [query,      setQuery]      = useState(() => searchParams.get('search') ?? '')
  const [typeFilter, setTypeFilter] = useState('')
  const [namespace,  setNamespace]  = useState('default')
  const [namespaces, setNamespaces] = useState<string[]>(['default'])
  const [sortBy,     setSortBy]     = useState<'name' | 'props_desc' | 'type'>('props_desc')
  const [compareSet, setCompareSet] = useState<EntityCard[]>([])
  const [showCompare, setShowCompare] = useState(false)

  useEffect(() => { document.title = '实体库 — PyroPlanner' }, [])

  useEffect(() => {
    fetchDocuments('__all__', 200).then(docs => {
      const ns = Array.from(new Set(docs.map(d => d.namespace))).sort()
      if (ns.length > 0) setNamespaces(ns)
    })
  }, [])

  useEffect(() => {
    setLoading(true)
    fetchEntities(namespace).then(data => {
      setEntities(data)
      setLoading(false)
    })
  }, [namespace])

  const entityTypes = useMemo(
    () => Array.from(new Set(entities.map(e => e.entity_type))).sort(),
    [entities],
  )

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim()
    return entities
      .filter(e => {
        const matchName = !q
          || e.canonical_name.toLowerCase().includes(q)
          || e.aliases.some(a => a.toLowerCase().includes(q))
        const matchType = !typeFilter || e.entity_type === typeFilter
        return matchName && matchType
      })
      .sort((a, b) => {
        if (sortBy === 'name')       return a.canonical_name.localeCompare(b.canonical_name, 'zh-CN')
        if (sortBy === 'props_desc') return b.properties.length - a.properties.length
        if (sortBy === 'type')       return a.entity_type.localeCompare(b.entity_type)
        return 0
      })
  }, [entities, query, typeFilter, sortBy])

  const toggleCompare = (card: EntityCard) => {
    setCompareSet(prev => {
      const existing = prev.find(c => c.id === card.id)
      if (existing) return prev.filter(c => c.id !== card.id)
      if (prev.length >= 5) return prev // max 5
      return [...prev, card]
    })
  }

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
          <span className="font-semibold text-sm">实体库</span>
        </div>
        <span className="text-[10px] text-ghost ml-1">
          共 {entities.length} 个已收录物质
        </span>
        {/* Compare button */}
        {compareSet.length >= 2 && (
          <button
            onClick={() => setShowCompare(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                       bg-blue-50 border border-blue-200 text-blue-500 hover:bg-blue-100 transition-all ml-1"
          >
            <BarChart2 size={12} />
            对比 {compareSet.length} 个实体
          </button>
        )}
        {compareSet.length > 0 && (
          <button
            onClick={() => setCompareSet([])}
            className="text-[10px] text-ghost hover:text-ink transition-colors"
          >
            清除选择
          </button>
        )}
        <NavTabs />
      </header>

      {/* Search + filter bar */}
      <div className="shrink-0 px-6 py-3 border-b border-line bg-surface space-y-2">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 flex-1 max-w-sm bg-muted rounded-xl px-3 py-2
                          border border-line focus-within:border-blue-300 transition-colors">
            <Search size={13} className="text-ghost shrink-0" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="搜索物质名称或别名…"
              className="bg-transparent text-sm text-ink placeholder-ghost outline-none flex-1"
            />
          </div>
          <select
            value={sortBy}
            onChange={e => setSortBy(e.target.value as typeof sortBy)}
            className="px-2.5 py-1.5 rounded-lg border border-line bg-surface text-xs text-dim
                       focus:outline-none focus:border-blue-300 transition-colors cursor-pointer"
          >
            <option value="props_desc">属性最多优先</option>
            <option value="name">名称 A→Z</option>
            <option value="type">按类型</option>
          </select>
          <div className="flex items-center gap-1.5 flex-wrap">
            {namespaces.map(ns => (
              <button
                key={ns}
                onClick={() => setNamespace(ns)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all
                            ${namespace === ns
                              ? 'bg-blue-50 border-blue-200 text-blue-500'
                              : 'border-line text-ghost hover:border-blue-200 hover:text-ink'
                            }`}
              >
                {ns}
              </button>
            ))}
          </div>
        </div>
        {/* Entity type pills */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => setTypeFilter('')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all
                        ${!typeFilter ? 'bg-blue-50 border-blue-200 text-blue-500' : 'border-line text-ghost hover:border-blue-200 hover:text-ink'}`}
          >
            全部类型
          </button>
          {entityTypes.map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(t === typeFilter ? '' : t)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all
                          ${typeFilter === t ? 'bg-blue-50 border-blue-200 text-blue-500' : 'border-line text-ghost hover:border-blue-200 hover:text-ink'}`}
            >
              {entityTypeLabel(t)}
            </button>
          ))}
        </div>
        {compareSet.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap pt-0.5">
            <span className="text-[10px] text-ghost">已选对比：</span>
            {compareSet.map((c, i) => (
              <span key={c.id} className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border"
                style={{ color: COMPARE_COLORS[i], borderColor: COMPARE_COLORS[i] + '40', background: COMPARE_COLORS[i] + '10' }}>
                {c.canonical_name}
                <button onClick={() => toggleCompare(c)} className="hover:opacity-70"><X size={9} /></button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Entity list */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2 text-ghost">
            <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">加载中…</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-16 text-ghost">
            <FlaskConical size={32} className="text-blue-100" />
            <p className="text-sm">
              {query ? `没有匹配"${query}"的实体` : `命名空间「${namespace}」暂无实体数据`}
            </p>
            {!query && (
              <button
                onClick={() => router.push('/ingest')}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium
                           bg-blue-50 border border-blue-200 text-blue-500 hover:bg-blue-100 transition-all"
              >
                前往摄取页上传文献
              </button>
            )}
          </div>
        ) : (
          <div className="max-w-3xl mx-auto grid grid-cols-1 gap-3 pb-6">
            {filtered.map(card => (
              <EntityCardView
                key={card.id}
                card={card}
                selected={compareSet.some(c => c.id === card.id)}
                onToggleSelect={toggleCompare}
                onViewGraph={name => router.push(`/graph?node=${encodeURIComponent(name)}`)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Compare panel modal */}
      {showCompare && compareSet.length >= 2 && (
        <ComparePanel cards={compareSet} onClose={() => setShowCompare(false)} />
      )}
    </div>
  )
}

export default function EntitiesPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-screen gap-2 text-ghost">
        <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
        <span className="text-sm">加载中…</span>
      </div>
    }>
      <EntitiesContent />
    </Suspense>
  )
}
