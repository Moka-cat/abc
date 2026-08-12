'use client'
import { useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Trash2, Clock, MessageCircle, Upload, FlaskConical, BookOpen, BarChart2, GitFork, Search, Download, PanelLeftClose, PanelLeftOpen, TestTube2, Microscope } from 'lucide-react'
import { loadHistory, deleteConversation, exportAllHistory, type ConversationEntry } from '@/lib/history'
import { fetchHealth } from '@/lib/api'

interface Props {
  selectedId: string | null
  onSelect: (entry: ConversationEntry) => void
  onDelete: (session_id: string) => void
  refreshKey: number
}

function timeLabel(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays === 0) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return `${diffDays}天前`
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

const NAV_ITEMS = [
  { href: '/',             icon: MessageCircle, label: '对话' },
  { href: '/ingest',       icon: Upload,        label: '摄取' },
  { href: '/documents',    icon: BookOpen,      label: '文档' },
  { href: '/entities',     icon: FlaskConical,  label: '实体库' },
  { href: '/experiments',  icon: TestTube2,     label: '实验' },
  { href: '/extract',      icon: Microscope,    label: '抽取' },
  { href: '/graph',        icon: GitFork,       label: '图谱' },
  { href: '/stats',        icon: BarChart2,     label: '统计' },
]

export default function Sidebar({ selectedId, onSelect, onDelete, refreshKey }: Props) {
  const router   = useRouter()
  const pathname = usePathname()
  const [entries, setEntries]             = useState<ConversationEntry[]>([])
  const [historySearch, setHistorySearch] = useState('')
  const [online, setOnline]               = useState<boolean | null>(null)
  const [collapsed, setCollapsed]         = useState(() => {
    try { return localStorage.getItem('sidebar_collapsed') === '1' } catch { return false }
  })

  useEffect(() => {
    setEntries(loadHistory())
  }, [refreshKey])

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      const ok = await fetchHealth()
      if (!cancelled) setOnline(ok)
    }
    check()
    const t = setInterval(check, 30_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  function handleDelete(e: React.MouseEvent, sid: string) {
    e.stopPropagation()
    deleteConversation(sid)
    setEntries(prev => prev.filter(en => en.session_id !== sid))
    onDelete(sid)
  }

  const toggleCollapse = () => {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem('sidebar_collapsed', next ? '1' : '0') } catch {}
  }

  if (collapsed) {
    return (
      <aside className="flex flex-col h-full w-14 shrink-0 bg-surface border-r border-line">
        {/* Collapsed: icon-only nav, 2-column grid */}
        <div className="grid grid-cols-2 gap-1 px-1.5 pt-3 pb-2">
          {NAV_ITEMS.map(({ href, icon: Icon, label }) => {
            const active = pathname === href
            return (
              <button key={href} onClick={() => router.push(href)} title={label}
                className={`h-8 flex items-center justify-center rounded-lg transition-all
                            ${active ? 'bg-blue-50 text-blue-500' : 'text-ghost hover:text-ink hover:bg-muted'}`}>
                <Icon size={14} />
              </button>
            )
          })}
        </div>
        <div className="flex-1" />
        {/* Expand button */}
        <div className="flex flex-col items-center pb-3 gap-2">
          <span className={`w-1.5 h-1.5 rounded-full ${online === null ? 'bg-gray-300' : online ? 'bg-green-400' : 'bg-red-400'}`} />
          <button onClick={toggleCollapse} title="展开侧边栏"
            className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all">
            <PanelLeftOpen size={14} />
          </button>
        </div>
      </aside>
    )
  }

  return (
    <aside className="flex flex-col h-full w-60 shrink-0 bg-surface border-r border-line">
      {/* Top navigation — 2-column grid */}
      <div className="px-3 pt-3 pb-2 shrink-0 grid grid-cols-4 gap-1">
        {NAV_ITEMS.map(({ href, icon: Icon, label }) => {
          const active = pathname === href
          return (
            <button
              key={href}
              onClick={() => router.push(href)}
              className={`flex flex-col items-center gap-1 py-2.5 rounded-xl
                          text-[10px] font-semibold transition-all duration-150
                          ${active
                            ? 'bg-blue-50 text-blue-500 border border-blue-100'
                            : 'text-ghost hover:text-ink hover:bg-muted border border-transparent'
                          }`}
            >
              <Icon size={15} />
              <span className="leading-none">{label}</span>
            </button>
          )
        })}
      </div>

      {/* Divider + history header + search (only on chat page) */}
      {pathname === '/' && (
      <div className="px-3 pt-2 pb-2 shrink-0 space-y-2">
        <div className="flex items-center gap-2 px-1">
          <MessageCircle size={13} className="text-brand" />
          <span className="text-[11px] font-semibold text-ghost tracking-widest uppercase flex-1">
            对话历史
          </span>
          {entries.length > 0 && (
            <button
              onClick={exportAllHistory}
              title="导出全部历史为 JSON"
              className="p-1 rounded-md text-ghost hover:text-ink hover:bg-muted transition-all"
            >
              <Download size={11} />
            </button>
          )}
        </div>
        <div className="flex items-center gap-1.5 bg-muted rounded-lg px-2 py-1.5
                        border border-line focus-within:border-blue-300 transition-colors">
          <Search size={11} className="text-ghost shrink-0" />
          <input
            value={historySearch}
            onChange={e => setHistorySearch(e.target.value)}
            placeholder="搜索历史…"
            className="bg-transparent text-[11px] text-ink placeholder-ghost outline-none flex-1 min-w-0"
          />
        </div>
      </div>
      )}

      {/* History list — only on chat page */}
      <div className={`flex-1 overflow-y-auto sidebar-scroll px-2 pb-4
                       ${pathname !== '/' ? 'hidden' : ''}`}>
        {entries.length === 0 && (
          <div className="flex flex-col items-center justify-center py-10 gap-2">
            <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center">
              <MessageCircle size={14} className="text-blue-300" />
            </div>
            <p className="text-[11px] text-ghost text-center">暂无历史对话</p>
          </div>
        )}
        {(() => {
          const q = historySearch.trim().toLowerCase()
          const filtered = entries.filter(en => !q || en.title.toLowerCase().includes(q))
          if (!filtered.length && entries.length > 0) return (
            <p className="text-[11px] text-ghost text-center py-6">无匹配结果</p>
          )

          // Group by date bucket
          const now = new Date()
          const bucket = (iso: string) => {
            const d = new Date(iso)
            const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000)
            if (diffDays === 0) return '今天'
            if (diffDays === 1) return '昨天'
            if (diffDays < 7)  return '本周'
            return '更早'
          }
          const ORDER = ['今天', '昨天', '本周', '更早']
          const groups: Record<string, ConversationEntry[]> = {}
          for (const e of filtered) {
            const b = bucket(e.created_at)
            ;(groups[b] ??= []).push(e)
          }

          return ORDER.filter(b => groups[b]).map(b => (
            <div key={b} className="mb-1">
              <p className="text-[9px] font-semibold text-ghost tracking-widest uppercase
                            px-3 py-1.5">{b}</p>
              <div className="space-y-0.5">
                {groups[b].map((entry) => {
          const active = selectedId === entry.session_id
          return (
            <div
              key={entry.session_id}
              onClick={() => onSelect(entry)}
              className={`group relative w-full text-left px-3 py-2.5 rounded-xl
                          flex items-start gap-2 cursor-pointer
                          transition-all duration-150
                          ${active
                            ? 'bg-blue-50 border border-blue-100 shadow-soft'
                            : 'hover:bg-muted border border-transparent'
                          }`}
            >
              {/* Active indicator */}
              {active && (
                <div
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-r-full"
                  style={{ background: 'linear-gradient(180deg, #38bdf8, #6366f1)' }}
                />
              )}

              <div className="min-w-0 flex-1">
                <p className={`text-xs truncate leading-snug pr-5 font-medium
                               ${active ? 'text-brand' : 'text-dim'}`}>
                  {entry.title}
                </p>
                <div className="flex items-center gap-1 mt-0.5">
                  <Clock size={9} className="text-ghost shrink-0" />
                  <p className="text-[10px] text-ghost truncate">
                    {timeLabel(entry.created_at)}
                    <span className="mx-1 opacity-60">·</span>
                    {entry.messages.filter(m => m.role === 'user').length} 条
                  </p>
                </div>
              </div>

              <button
                onClick={(e) => handleDelete(e, entry.session_id)}
                className="absolute right-2 top-2.5 opacity-0 group-hover:opacity-100
                           p-0.5 rounded-md text-ghost hover:text-red-400
                           hover:bg-red-50 transition-all duration-150"
                title="删除"
              >
                <Trash2 size={11} />
              </button>
                </div>
                )
              })}
              </div>
            </div>
          ))
        })()}
      </div>
      {/* Backend status + collapse */}
      <div className="shrink-0 px-4 py-2.5 border-t border-line flex items-center gap-2">
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors duration-500
            ${online === null ? 'bg-gray-300' : online ? 'bg-green-400' : 'bg-red-400'}`}
        />
        <span className="text-[10px] text-ghost truncate flex-1">
          {online === null ? '连接中…' : online ? '服务正常' : '服务不可达'}
        </span>
        <button onClick={toggleCollapse} title="折叠侧边栏"
          className="p-1 rounded-md text-ghost hover:text-ink hover:bg-muted transition-all shrink-0">
          <PanelLeftClose size={12} />
        </button>
      </div>
    </aside>
  )
}
