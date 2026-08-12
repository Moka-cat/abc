'use client'
/**
 * ChatChart — parse an assistant message's markdown content and render charts.
 *
 * Detects:
 *   1. Markdown tables with ≥1 numeric column → BarChart (grouped or single)
 *   2. Percentage patterns (e.g. "AP: 68%", "**HTPB** 14%") → PieChart
 *   3. Numeric property lists (bold label + value) → HorizontalBar
 *
 * Falls back gracefully when nothing parseable is found.
 */
import { useMemo, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, Legend,
  PieChart, Pie, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from 'recharts'

const PALETTE = [
  '#38bdf8', '#6366f1', '#2dd4bf', '#fb923c', '#a78bfa',
  '#34d399', '#f472b6', '#facc15', '#60a5fa', '#f87171',
]

// ─── Parsers ──────────────────────────────────────────────────────────────────

interface TableData {
  headers: string[]
  rows: string[][]
}

/** Extract ALL markdown tables from content */
function parseMarkdownTables(content: string): TableData[] {
  const tables: TableData[] = []
  const lines = content.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (line.startsWith('|') && line.endsWith('|') && line.includes('|', 1)) {
      const headers = line.split('|').slice(1, -1).map(h => h.replace(/\*/g, '').trim())
      // Expect separator next
      if (
        i + 1 < lines.length &&
        /^\|[\s\-:|]+\|$/.test(lines[i + 1].trim().replace(/\s/g, ''))
      ) {
        const rows: string[][] = []
        let j = i + 2
        while (j < lines.length) {
          const row = lines[j].trim()
          if (!row.startsWith('|') || !row.endsWith('|')) break
          const cells = row.split('|').slice(1, -1).map(c => c.replace(/\*/g, '').trim())
          rows.push(cells)
          j++
        }
        if (rows.length > 0) tables.push({ headers, rows })
        i = j
        continue
      }
    }
    i++
  }
  return tables
}

function isNumeric(s: string): boolean {
  return /^-?\d+(\.\d+)?(%|g\/cm³|km\/s|GPa|kJ\/kg|mm\/s|°C|K|s|MPa|m\/s)?$/.test(s.trim())
}

function toNumber(s: string): number {
  return parseFloat(s.replace(/[^0-9.-]/g, ''))
}

interface ParsedRow { name: string; [key: string]: string | number }

/** Convert table data into recharts-compatible objects */
function tableToChartData(table: TableData): {
  data: ParsedRow[]
  numericKeys: string[]
  nameKey: string
} | null {
  if (table.headers.length < 2 || table.rows.length < 1) return null

  // Find first text column (name column) and numeric columns
  const colTypes = table.headers.map((_, ci) =>
    table.rows.some(row => isNumeric(row[ci] ?? '')) ? 'numeric' : 'text'
  )
  const nameIdx = colTypes.findIndex(t => t === 'text')
  const numIdxs = colTypes.map((t, i) => t === 'numeric' ? i : -1).filter(i => i >= 0)

  if (numIdxs.length === 0) return null

  const nameKey = nameIdx >= 0 ? table.headers[nameIdx] : '项目'
  const numericKeys = numIdxs.map(i => table.headers[i])

  const data: ParsedRow[] = table.rows.map((row, ri) => {
    const obj: ParsedRow = { name: nameIdx >= 0 ? (row[nameIdx] ?? `#${ri + 1}`) : `#${ri + 1}` }
    numIdxs.forEach(ci => {
      const val = row[ci] ?? ''
      obj[table.headers[ci]] = isNumeric(val) ? toNumber(val) : val
    })
    return obj
  })

  return { data, numericKeys, nameKey }
}

interface PieDatum { name: string; value: number }

/** Extract "Label: xx%" or "**Label** xx%" patterns */
function parsePercentages(content: string): PieDatum[] {
  const results: PieDatum[] = []
  // Pattern: optional ** + label + ** + colon/space + number + %
  const re = /\*{0,2}([A-Za-z\u4e00-\u9fa5][^*\n|:：，,。\d]{0,20}?)\*{0,2}\s*[：:]\s*(\d+(?:\.\d+)?)\s*%/g
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    const label = m[1].trim()
    const value = parseFloat(m[2])
    if (label && value > 0 && value <= 100) {
      // Avoid duplicates
      if (!results.find(r => r.name === label)) {
        results.push({ name: label, value })
      }
    }
  }
  // Only return if sum is plausibly a whole (50–110%)
  const sum = results.reduce((a, r) => a + r.value, 0)
  return sum >= 50 && sum <= 110 ? results : []
}

// ─── Chart renderers ──────────────────────────────────────────────────────────

function PieViz({ data }: { data: PieDatum[] }) {
  const total = data.reduce((a, d) => a + d.value, 0)
  return (
    <div>
      <p className="text-[10px] text-ghost font-semibold tracking-widest uppercase mb-2">
        配方组成（总计 {total.toFixed(1)}%）
      </p>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={data}
            cx="50%" cy="48%"
            innerRadius={52} outerRadius={78}
            paddingAngle={3}
            dataKey="value"
            label={({ name, value }) => `${name} ${value}%`}
            labelLine={false}
          >
            {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
          </Pie>
          <Tooltip
            formatter={(v) => [`${v}%`, '占比']}
            contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }}
          />
          <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}

function TableBarViz({ table }: { table: TableData }) {
  const parsed = tableToChartData(table)
  if (!parsed) return null
  const { data, numericKeys } = parsed

  const isHorizontal = data.length > 5
  const height = isHorizontal ? Math.max(160, data.length * 30) : 220
  const single = numericKeys.length === 1

  if (isHorizontal) {
    return (
      <div>
        <p className="text-[10px] text-ghost font-semibold tracking-widest uppercase mb-2">
          {table.headers[0]} 对比
        </p>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} layout="vertical" barCategoryGap="25%">
            <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} width={90} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
            {numericKeys.map((k, i) => (
              <Bar key={k} dataKey={k} fill={PALETTE[i % PALETTE.length]} radius={[0, 6, 6, 0]} />
            ))}
            {!single && <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />}
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  return (
    <div>
      <p className="text-[10px] text-ghost font-semibold tracking-widest uppercase mb-2">
        {table.headers[0]} 对比
      </p>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} barCategoryGap="30%">
          <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={32} />
          <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
          {numericKeys.map((k, i) => (
            <Bar key={k} dataKey={k} fill={PALETTE[i % PALETTE.length]} radius={[6, 6, 0, 0]}>
              {single && data.map((_, di) => <Cell key={di} fill={PALETTE[di % PALETTE.length]} />)}
            </Bar>
          ))}
          {!single && <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function TableRadarViz({ table }: { table: TableData }) {
  const parsed = tableToChartData(table)
  if (!parsed || parsed.numericKeys.length < 3) return null
  const { data, numericKeys } = parsed
  if (data.length < 2) return null

  // Radar: rows = entities, columns = axes
  // Normalize each column to 0–100
  const maxes: Record<string, number> = {}
  numericKeys.forEach(k => {
    maxes[k] = Math.max(...data.map(d => (d[k] as number) || 0))
  })

  const radarData = numericKeys.map(k => {
    const row: Record<string, number | string> = { axis: k }
    data.forEach(d => { row[d.name as string] = maxes[k] > 0 ? Math.round(((d[k] as number) / maxes[k]) * 100) : 0 })
    return row
  })

  return (
    <div>
      <p className="text-[10px] text-ghost font-semibold tracking-widest uppercase mb-2">
        综合性能雷达（归一化）
      </p>
      <ResponsiveContainer width="100%" height={240}>
        <RadarChart data={radarData}>
          <PolarGrid stroke="#e2e8f0" />
          <PolarAngleAxis dataKey="axis" tick={{ fontSize: 11, fill: '#64748b' }} />
          <PolarRadiusAxis angle={90} domain={[0, 100]} tick={{ fontSize: 9, fill: '#94a3b8' }} tickCount={4} />
          {data.map((d, i) => (
            <Radar
              key={d.name as string}
              name={d.name as string}
              dataKey={d.name as string}
              stroke={PALETTE[i % PALETTE.length]}
              fill={PALETTE[i % PALETTE.length]}
              fillOpacity={0.12}
              strokeWidth={2}
            />
          ))}
          <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #e2e8f0', fontSize: 12 }} />
          <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ─── Main export ──────────────────────────────────────────────────────────────

export type ChartKind = 'bar' | 'radar' | 'pie'

interface ChartOption { kind: ChartKind; label: string }

interface Props { content: string }

export default function ChatChart({ content }: Props) {
  const { tables, pieData, chartOptions } = useMemo(() => {
    const tables = parseMarkdownTables(content)
    const pieData = parsePercentages(content)

    const options: ChartOption[] = []
    if (pieData.length >= 2) options.push({ kind: 'pie', label: '配方饼图' })
    tables.forEach((t, i) => {
      const p = tableToChartData(t)
      if (!p) return
      options.push({ kind: 'bar', label: tables.length > 1 ? `表${i + 1}柱状图` : '柱状图' })
      if (p.numericKeys.length >= 3 && p.data.length >= 2) {
        options.push({ kind: 'radar', label: tables.length > 1 ? `表${i + 1}雷达图` : '雷达图' })
      }
    })

    return { tables, pieData, chartOptions: options }
  }, [content])

  const [activeKind, setActiveKind] = useState<ChartKind | null>(null)

  if (chartOptions.length === 0) return null

  return (
    <div className="mt-3">
      {/* Toggle pills */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-ghost">图表：</span>
        {chartOptions.map(opt => (
          <button
            key={opt.kind + opt.label}
            onClick={() => setActiveKind(prev => prev === opt.kind ? null : opt.kind)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold
                        border transition-all duration-150
                        ${activeKind === opt.kind
                          ? 'bg-blue-50 border-blue-200 text-blue-600'
                          : 'border-line text-ghost hover:border-blue-200 hover:text-blue-500 hover:bg-blue-50'
                        }`}
          >
            📊 {opt.label}
          </button>
        ))}
      </div>

      {/* Chart panel */}
      {activeKind && (
        <div className="mt-3 bg-muted/60 rounded-xl border border-line px-4 py-3 space-y-4">
          {activeKind === 'pie' && pieData.length >= 2 && <PieViz data={pieData} />}
          {activeKind === 'bar' && tables.map((t, i) => {
            const p = tableToChartData(t)
            return p ? <TableBarViz key={i} table={t} /> : null
          })}
          {activeKind === 'radar' && tables.map((t, i) => {
            const p = tableToChartData(t)
            return p && p.numericKeys.length >= 3 ? <TableRadarViz key={i} table={t} /> : null
          })}
        </div>
      )}
    </div>
  )
}
