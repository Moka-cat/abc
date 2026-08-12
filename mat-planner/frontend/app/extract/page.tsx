'use client'
import { useCallback, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowLeft, Microscope, Upload, FileText, FileJson, Download,
  FlaskConical, BarChart2, Table2, ChevronDown, ChevronRight, X, Loader2,
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import { extractFile, type ExtractionResult, type ExtractedEntity } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

const PALETTE = ['#38bdf8', '#6366f1', '#2dd4bf', '#fb923c', '#a78bfa', '#34d399', '#f472b6', '#facc15']

const ENTITY_TYPE_LABEL: Record<string, string> = {
  compound: '化合物', propellant: '推进剂', oxidizer: '氧化剂',
  binder: '粘合剂', fuel: '燃料', ingredient: '成分', material: '材料',
}
const ENTITY_TYPE_COLOR: Record<string, string> = {
  compound:   'bg-blue-50 text-blue-500 border-blue-200',
  oxidizer:   'bg-orange-50 text-orange-500 border-orange-200',
  binder:     'bg-teal-50 text-teal-600 border-teal-200',
  fuel:       'bg-amber-50 text-amber-600 border-amber-200',
  propellant: 'bg-violet-50 text-violet-500 border-violet-200',
  ingredient: 'bg-gray-50 text-gray-500 border-gray-200',
}

function typeColor(t: string) { return ENTITY_TYPE_COLOR[t] ?? 'bg-gray-50 text-gray-500 border-gray-200' }
function typeLabel(t: string) { return ENTITY_TYPE_LABEL[t] ?? t }

// ── Sub-components ────────────────────────────────────────────────────────────

function EntityCard({ entity }: { entity: ExtractedEntity }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden hover:border-blue-200 transition-all">
      <div className="flex items-center gap-3 px-4 py-3 cursor-pointer" onClick={() => setOpen(o => !o)}>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold text-white shrink-0"
          style={{ background: 'linear-gradient(135deg,#38bdf8,#6366f1)' }}>
          {entity.name.slice(0, 2).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-ink">{entity.name}</span>
            <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${typeColor(entity.entity_type)}`}>
              {typeLabel(entity.entity_type)}
            </span>
            {entity.properties.length > 0 && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-ghost border border-line shrink-0">
                {entity.properties.length} 个属性
              </span>
            )}
          </div>
          {entity.aliases.length > 0 && (
            <p className="text-[10px] text-ghost mt-0.5 truncate">
              别名：{entity.aliases.slice(0, 4).join(' · ')}
            </p>
          )}
        </div>
        <div className="text-ghost">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</div>
      </div>
      {open && entity.properties.length > 0 && (
        <div className="border-t border-line overflow-x-auto">
          <table className="w-full text-xs" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
            <thead>
              <tr className="bg-slate-50/80">
                <th className="px-3 py-2 text-left text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">属性</th>
                <th className="px-3 py-2 text-right text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">数值</th>
                <th className="px-3 py-2 text-left text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">单位</th>
                <th className="px-3 py-2 text-left text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">条件</th>
                <th className="px-3 py-2 text-center text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">置信度</th>
                <th className="px-3 py-2 text-left text-ghost font-semibold border-b border-line text-[10px] whitespace-nowrap">原文引用</th>
              </tr>
            </thead>
            <tbody>
              {entity.properties.map((p, i) => {
                const conf = p.confidence
                const confColor = conf == null ? 'text-ghost' : conf >= 0.85 ? 'text-green-500' : conf >= 0.7 ? 'text-amber-500' : 'text-red-400'
                const confLabel = conf == null ? '—' : `${Math.round(conf * 100)}%`
                return (
                  <tr key={i} className={`transition-colors hover:bg-blue-50/40 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                    <td className="px-3 py-2 font-medium text-dim whitespace-nowrap border-b border-line/40">{p.property}</td>
                    <td className="px-3 py-2 text-right font-semibold text-blue-600 tabular-nums whitespace-nowrap border-b border-line/40">
                      {p.value !== null && p.value !== undefined ? String(p.value) : <span className="text-ghost/40">—</span>}
                    </td>
                    <td className="px-3 py-2 text-ghost whitespace-nowrap border-b border-line/40">{p.unit ?? <span className="text-ghost/40">—</span>}</td>
                    <td className="px-3 py-2 text-ghost text-[10px] whitespace-nowrap border-b border-line/40">
                      {p.condition ? Object.entries(p.condition).map(([k,v]) => `${k}=${v}`).join(', ') : <span className="text-ghost/40">—</span>}
                    </td>
                    <td className={`px-3 py-2 text-center font-semibold tabular-nums text-[11px] border-b border-line/40 ${confColor}`}>
                      {confLabel}
                    </td>
                    <td className="px-3 py-2 text-ghost text-[10px] border-b border-line/40 max-w-[220px]">
                      <span className="line-clamp-2" title={p.quote ?? ''}>
                        {p.quote ? `"${p.quote}"` : <span className="text-ghost/40">—</span>}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {open && entity.properties.length === 0 && (
        <p className="px-4 pb-3 text-xs text-ghost">（在本文件中未检测到数值属性）</p>
      )}
    </div>
  )
}

function TableView({ table, index }: { table: import('@/lib/api').ExtractedTable; index: number }) {
  const [open, setOpen] = useState(true)
  const [showChart, setShowChart] = useState(false)

  // Normalize: ensure all rows have same column count as headers
  const colCount = Math.max(table.headers.length, ...table.rows.map(r => r.length))
  const headers = table.headers.length > 0
    ? [...table.headers, ...Array(Math.max(0, colCount - table.headers.length)).fill('')]
    : Array.from({ length: colCount }, (_, i) => `列 ${i + 1}`)
  const rows = table.rows.map(row =>
    [...row, ...Array(Math.max(0, colCount - row.length)).fill('')]
  )

  // Detect numeric columns for chart
  const numCols = headers.reduce<number[]>((acc, _, ci) => {
    const vals = rows.map(r => r[ci] ?? '')
    const numCount = vals.filter(v => v.trim() !== '' && !isNaN(parseFloat(v))).length
    if (numCount >= rows.length * 0.5) acc.push(ci)
    return acc
  }, [])
  const nameCols = headers.map((_, ci) => ci).filter(ci => !numCols.includes(ci))
  const nameCol = nameCols[0] ?? -1
  const numCol  = numCols[0] ?? -1
  const canChart = nameCol >= 0 && numCol >= 0 && rows.length >= 2 && rows.length <= 30
  const chartData = canChart ? rows.slice(0, 20).map(row => ({
    name: (row[nameCol] ?? '').slice(0, 20),
    value: parseFloat(row[numCol] ?? '0') || 0,
  })).filter(d => d.name) : []

  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden shadow-soft">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/30 transition-colors select-none"
        onClick={() => setOpen(o => !o)}>
        <div className="w-7 h-7 rounded-lg bg-violet-50 border border-violet-100 flex items-center justify-center shrink-0">
          <Table2 size={13} className="text-violet-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-ink">表 {index + 1}</span>
            {table.caption && (
              <span className="text-xs text-dim truncate max-w-[300px]" title={table.caption}>
                {table.caption}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-ghost">{colCount} 列</span>
            <span className="text-[10px] text-ghost/50">·</span>
            <span className="text-[10px] text-ghost">{rows.length} 行</span>
            {canChart && (
              <>
                <span className="text-[10px] text-ghost/50">·</span>
                <span className="text-[10px] text-violet-400">含数值列</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {canChart && open && (
            <button
              onClick={e => { e.stopPropagation(); setShowChart(c => !c) }}
              className={`px-2 py-0.5 rounded text-[10px] font-medium border transition-all
                          ${showChart
                            ? 'bg-violet-50 border-violet-200 text-violet-500'
                            : 'border-line text-ghost hover:border-violet-200 hover:text-violet-500'
                          }`}
            >
              图表
            </button>
          )}
          <div className="text-ghost ml-1">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</div>
        </div>
      </div>

      {open && (
        <div className="border-t border-line">
          {/* Optional chart */}
          {canChart && showChart && chartData.length > 0 && (
            <div className="px-4 py-3 border-b border-line bg-muted/20">
              <p className="text-[10px] text-ghost font-semibold uppercase tracking-widest mb-2">
                {headers[numCol]} 分布
              </p>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={chartData} barCategoryGap="30%">
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={36} />
                  <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                  <Bar dataKey="value" name={headers[numCol]} radius={[4, 4, 0, 0]}>
                    {chartData.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
              <thead>
                <tr className="bg-gradient-to-r from-slate-50 to-gray-50">
                  <th className="px-3 py-2 text-center text-ghost/60 font-medium border-b border-r border-line w-10 text-[10px]">
                    #
                  </th>
                  {headers.map((h, i) => (
                    <th key={i}
                      className="px-3 py-2.5 text-left text-dim font-semibold border-b border-r border-line
                                 whitespace-nowrap text-[11px] last:border-r-0">
                      {h || <span className="text-ghost/40 italic">列 {i + 1}</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={colCount + 1}
                      className="px-4 py-6 text-center text-ghost text-xs">
                      （无数据行）
                    </td>
                  </tr>
                ) : (
                  rows.map((row, ri) => (
                    <tr key={ri}
                      className={`transition-colors hover:bg-blue-50/40
                                  ${ri % 2 === 0 ? 'bg-white' : 'bg-slate-50/60'}`}>
                      <td className="px-3 py-2 text-center text-ghost/40 font-mono text-[10px] border-b border-r border-line/50 w-10">
                        {ri + 1}
                      </td>
                      {row.map((cell, ci) => {
                        const isNum = numCols.includes(ci)
                        return (
                          <td key={ci}
                            className={`px-3 py-2 border-b border-r border-line/50 last:border-r-0
                                        ${isNum ? 'text-right font-semibold text-blue-600 tabular-nums' : 'text-dim'}
                                        ${cell.length > 60 ? 'min-w-[200px] whitespace-normal break-words' : 'whitespace-nowrap'}`}
                          >
                            {cell || <span className="text-ghost/30">—</span>}
                          </td>
                        )
                      })}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {rows.length > 15 && (
            <div className="px-4 py-2 border-t border-line bg-muted/30 text-center">
              <span className="text-[10px] text-ghost">共 {rows.length} 行数据</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Export helpers ────────────────────────────────────────────────────────────

function exportJSON(result: ExtractionResult) {
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `extract-${result.filename}.json`; a.click()
  URL.revokeObjectURL(url)
}

function exportCSV(result: ExtractionResult) {
  const rows: string[] = ['实体,类型,属性,数值,单位,条件,原文引用']
  for (const entity of result.entities) {
    if (entity.properties.length === 0) {
      rows.push(`"${entity.name}","${entity.entity_type}","","","","",""`)
    } else {
      for (const p of entity.properties) {
        const cond = p.condition ? Object.entries(p.condition).map(([k,v])=>`${k}=${v}`).join(';') : ''
        rows.push([
          entity.name, entity.entity_type, p.property,
          String(p.value ?? ''), p.unit ?? '', cond, (p.quote ?? '').replace(/"/g, '""'),
        ].map(v => `"${v}"`).join(','))
      }
    }
  }
  const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `extract-${result.filename}.csv`; a.click()
  URL.revokeObjectURL(url)
}

// ── Main page ─────────────────────────────────────────────────────────────────

type TabType = 'entities' | 'tables' | 'preview' | 'chart'

export default function ExtractPage() {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging]     = useState(false)
  const [loading,  setLoading]      = useState(false)
  const [error,    setError]        = useState('')
  const [result,   setResult]       = useState<ExtractionResult | null>(null)
  const [tab,      setTab]          = useState<TabType>('entities')

  const handleFile = useCallback(async (file: File) => {
    setError('')
    setResult(null)
    setLoading(true)
    try {
      const res = await extractFile(file)
      setResult(res)
      setTab(res.entities.length > 0 ? 'entities' : res.tables.length > 0 ? 'tables' : 'preview')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '抽取失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  // Property distribution chart data
  const propDistData = result
    ? Object.entries(
        result.entities.flatMap(e => e.properties.map(p => p.property))
          .reduce<Record<string,number>>((acc, p) => { acc[p] = (acc[p] ?? 0) + 1; return acc }, {})
      ).sort((a,b) => b[1]-a[1]).slice(0,10).map(([name,count]) => ({ name, count }))
    : []

  const TABS: { id: TabType; label: string; count?: number }[] = [
    { id: 'entities', label: '实体 & 属性', count: result?.text_stats.entity_count },
    { id: 'tables',   label: '表格',        count: result?.text_stats.table_count },
    { id: 'chart',    label: '属性分布图' },
    { id: 'preview',  label: '原文预览' },
  ]

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
          <Microscope size={14} className="text-brand" />
          <span className="font-semibold text-sm">数据抽取</span>
        </div>
        <span className="text-[10px] text-ghost ml-1">上传 PDF 或 JSON，即时抽取实体与属性数据</span>
        {result && (
          <div className="flex items-center gap-2 ml-auto">
            <button onClick={() => exportCSV(result)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                         border border-line text-ghost hover:text-ink hover:border-blue-200 hover:bg-blue-50 transition-all">
              <Download size={12} /> CSV
            </button>
            <button onClick={() => exportJSON(result)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                         border border-line text-ghost hover:text-ink hover:border-blue-200 hover:bg-blue-50 transition-all">
              <Download size={12} /> JSON
            </button>
            <button onClick={() => { setResult(null); setError('') }}
              className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all">
              <X size={13} />
            </button>
          </div>
        )}
        <NavTabs />
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-4xl mx-auto space-y-5 pb-6">

          {/* Drop zone */}
          {!result && (
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => !loading && inputRef.current?.click()}
            className={`flex flex-col items-center justify-center gap-4 py-16 rounded-2xl
                        border-2 border-dashed cursor-pointer transition-all duration-200
                        ${loading ? 'border-blue-300 bg-blue-50/50 cursor-wait'
                          : dragging ? 'border-blue-400 bg-blue-50'
                          : 'border-line bg-surface hover:border-blue-300 hover:bg-blue-50/30'
                        }`}
          >
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-xl flex items-center justify-center shadow-soft"
                style={{ background: 'linear-gradient(135deg,#38bdf8,#6366f1)' }}>
                {loading
                  ? <Loader2 size={24} className="text-white animate-spin" />
                  : <Microscope size={24} className="text-white" />
                }
              </div>
              <div>
                <p className="font-semibold text-ink">
                  {loading ? '正在抽取，请稍候…' : '拖拽文件到此处，或点击选择'}
                </p>
                <p className="text-sm text-ghost mt-1">
                  {loading
                    ? 'LLM 正在识别实体和属性，通常需要 15-60 秒'
                    : '支持 PDF（科学文献）和 JSON（MinerU 解析结果）格式'
                  }
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-muted border border-line text-xs text-ghost">
                <FileText size={12} className="text-blue-400" /> PDF
              </div>
              <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-muted border border-line text-xs text-ghost">
                <FileJson size={12} className="text-green-500" /> JSON
              </div>
            </div>
            {error && (
              <p className="text-sm text-red-500 bg-red-50 px-4 py-2 rounded-xl border border-red-100">
                {error}
              </p>
            )}
            <input ref={inputRef} type="file" accept=".pdf,.json" className="hidden"
              onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }} />
          </div>
          )}

          {/* Results */}
          {result && (
          <>
            {/* Stats bar */}
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: '实体数',   value: result.text_stats.entity_count,   icon: <FlaskConical size={15} className="text-blue-400" />,  color: 'from-blue-400 to-blue-500' },
                { label: '属性数',   value: result.text_stats.property_count,  icon: <BarChart2    size={15} className="text-teal-400" />,   color: 'from-teal-400 to-teal-500' },
                { label: '表格数',   value: result.text_stats.table_count,     icon: <Table2       size={15} className="text-violet-400" />, color: 'from-violet-400 to-indigo-500' },
                { label: '文本字符', value: result.text_stats.char_count.toLocaleString(), icon: <FileText size={15} className="text-orange-400" />, color: 'from-orange-400 to-orange-500' },
              ].map(({ label, value, icon, color }) => (
                <div key={label} className="bg-surface border border-line rounded-xl px-4 py-3 flex items-center gap-3">
                  <div className={`w-9 h-9 rounded-lg bg-gradient-to-br ${color} flex items-center justify-center shrink-0 shadow-soft`}>
                    <span className="text-white">{icon}</span>
                  </div>
                  <div>
                    <p className="text-xl font-bold text-ink leading-tight">{value}</p>
                    <p className="text-[10px] text-ghost">{label}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* File badge */}
            <div className="flex items-center gap-2 text-xs text-ghost">
              {result.file_type === 'pdf' ? <FileText size={12} className="text-blue-400" /> : <FileJson size={12} className="text-green-500" />}
              <span className="font-medium text-dim">{result.filename}</span>
              <span className="px-1.5 py-0.5 bg-muted rounded text-[10px] border border-line uppercase">{result.file_type}</span>
              <button onClick={() => { setResult(null); setError('') }}
                className="ml-auto text-ghost hover:text-ink hover:bg-muted px-2 py-1 rounded-lg transition-all text-[10px]">
                重新上传
              </button>
            </div>

            {/* Tab bar */}
            <div className="flex items-center gap-1 border-b border-line">
              {TABS.map(t => (
                <button key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-all
                              ${tab === t.id
                                ? 'border-blue-400 text-blue-500'
                                : 'border-transparent text-ghost hover:text-ink'
                              }`}
                >
                  {t.label}
                  {t.count !== undefined && t.count > 0 && (
                    <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold
                                      ${tab === t.id ? 'bg-blue-100 text-blue-500' : 'bg-muted text-ghost'}`}>
                      {t.count}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Tab content */}
            {tab === 'entities' && (
              <div className="space-y-3">
                {result.entities.length === 0 ? (
                  <div className="flex flex-col items-center gap-2 py-12 text-ghost">
                    <FlaskConical size={32} className="text-blue-100" />
                    <p className="text-sm">未检测到实体数据</p>
                    <p className="text-xs text-ghost max-w-xs text-center">
                      可能文件中没有含能材料相关术语，或 LLM NER 未启用
                    </p>
                  </div>
                ) : (
                  result.entities.map((entity, i) => <EntityCard key={i} entity={entity} />)
                )}
              </div>
            )}

            {tab === 'tables' && (
              <div className="space-y-4">
                {result.tables.length === 0 ? (
                  <div className="flex flex-col items-center gap-2 py-12 text-ghost">
                    <Table2 size={32} className="text-blue-100" />
                    <p className="text-sm">未检测到表格</p>
                  </div>
                ) : (
                  result.tables.map((table, i) => <TableView key={i} table={table} index={i} />)
                )}
              </div>
            )}

            {tab === 'chart' && (
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-4">
                  属性出现频次 Top {propDistData.length}
                </p>
                {propDistData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={propDistData} layout="vertical" barCategoryGap="30%">
                      <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
                      <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={120} axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
                      <Bar dataKey="count" name="出现次数" radius={[0, 6, 6, 0]}>
                        {propDistData.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-sm text-ghost text-center py-8">无属性数据</p>
                )}
              </div>
            )}

            {tab === 'preview' && (
              <div className="bg-surface border border-line rounded-xl px-5 py-4">
                <p className="text-[10px] font-semibold text-ghost tracking-widest uppercase mb-3">
                  原文预览（前 800 字符）
                </p>
                <pre className="text-xs text-dim whitespace-pre-wrap leading-relaxed font-mono bg-muted rounded-lg px-4 py-3 overflow-auto max-h-96">
                  {result.preview}
                </pre>
              </div>
            )}
          </>
          )}

        </div>
      </div>
    </div>
  )
}
