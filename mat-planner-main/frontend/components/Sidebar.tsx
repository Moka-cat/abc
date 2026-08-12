'use client'
import { useEffect, useState } from 'react'
import { Trash2, Clock, MessageCircle } from 'lucide-react'
import { loadHistory, deleteConversation, type ConversationEntry } from '@/lib/history'

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

export default function Sidebar({ selectedId, onSelect, onDelete, refreshKey }: Props) {
  const [entries, setEntries] = useState<ConversationEntry[]>([])

  useEffect(() => {
    setEntries(loadHistory())
  }, [refreshKey])

  function handleDelete(e: React.MouseEvent, sid: string) {
    e.stopPropagation()
    deleteConversation(sid)
    setEntries(prev => prev.filter(en => en.session_id !== sid))
    onDelete(sid)
  }

  return (
    <aside className="flex flex-col h-full w-60 shrink-0 bg-surface border-r border-line">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 shrink-0">
        <div className="flex items-center gap-2">
          <MessageCircle size={13} className="text-brand" />
          <span className="text-[11px] font-semibold text-ghost tracking-widest uppercase">
            对话历史
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto sidebar-scroll px-2 pb-4 space-y-0.5">
        {entries.length === 0 && (
          <div className="flex flex-col items-center justify-center py-10 gap-2">
            <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center">
              <MessageCircle size={14} className="text-blue-300" />
            </div>
            <p className="text-[11px] text-ghost text-center">暂无历史对话</p>
          </div>
        )}
        {entries.map((entry) => {
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
    </aside>
  )
}
