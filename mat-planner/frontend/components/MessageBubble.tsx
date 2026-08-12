'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, Check, RefreshCw, BookOpen, Database, FileText, ChevronDown, ExternalLink } from 'lucide-react'
import PlanBar from './PlanBar'
import ChatChart from './ChatChart'
import type { PlanStep, MessageSource } from '@/lib/api'

export interface Message {
  role: 'user' | 'assistant'
  content: string
  plan?: PlanStep[]
  streaming?: boolean
  phase?: 'idle' | 'analyzing' | 'planning' | 'done'
  statusText?: string
  elapsedSecs?: number
  phase1Secs?: number
  agentLabel?: string
  agentIcon?:  string
  agentIntent?: string
  agentReason?: string
  sources?: MessageSource[]
}

// Per-intent accent colour (Tailwind classes)
const INTENT_STYLE: Record<string, { bg: string; border: string; text: string }> = {
  literature: { bg: 'bg-blue-50',   border: 'border-blue-200',   text: 'text-blue-500' },
  experiment: { bg: 'bg-violet-50', border: 'border-violet-200', text: 'text-violet-500' },
  data:       { bg: 'bg-teal-50',   border: 'border-teal-200',   text: 'text-teal-600' },
  hybrid:     { bg: 'bg-orange-50', border: 'border-orange-200', text: 'text-orange-500' },
  modify:     { bg: 'bg-amber-50',  border: 'border-amber-200',  text: 'text-amber-600' },
  safety:     { bg: 'bg-red-50',    border: 'border-red-200',    text: 'text-red-500' },
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1 ml-0.5">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="thinking-dot"
          style={{
            background: 'linear-gradient(135deg, #38bdf8, #6366f1)',
            animationDelay: `${i * 0.2}s`,
          }}
        />
      ))}
    </span>
  )
}

function AgentBadge({
  label, icon, intent, reason,
}: {
  label: string; icon: string; intent?: string; reason?: string
}) {
  const style = INTENT_STYLE[intent ?? 'literature'] ?? INTENT_STYLE.literature
  return (
    <div className="flex flex-col gap-0.5">
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full self-start
                    text-[10px] font-semibold border
                    ${style.bg} ${style.border} ${style.text}`}
      >
        <span>{icon}</span>
        {label}
      </span>
      {reason && (
        <span className="text-[10px] text-ghost px-1 leading-snug">{reason}</span>
      )}
    </div>
  )
}

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button
      onClick={handleCopy}
      title="复制"
      className="opacity-0 group-hover:opacity-100 absolute -top-2 right-2
                 p-1 rounded-md bg-surface border border-line shadow-soft
                 text-ghost hover:text-ink transition-all duration-150"
    >
      {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
    </button>
  )
}

function SourcesCard({ sources }: { sources: MessageSource[] }) {
  const [open, setOpen] = useState(false)
  const router = useRouter()
  if (!sources.length) return null

  const iconFor = (kind: string) => {
    if (kind === 'entity') return <Database size={10} className="text-teal-500 shrink-0" />
    return <FileText size={10} className="text-blue-400 shrink-0" />
  }

  const handleOpen = (s: MessageSource) => {
    if (s.kind === 'entity') {
      router.push(`/entities?search=${encodeURIComponent(s.title)}`)
    } else if (s.doc_id) {
      router.push(`/documents?id=${encodeURIComponent(s.doc_id)}`)
    } else {
      router.push(`/documents?q=${encodeURIComponent(s.title)}`)
    }
  }

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-[10px] text-ghost hover:text-ink transition-colors"
      >
        <BookOpen size={11} className="text-blue-300" />
        <span>{sources.length} 个参考来源</span>
        <ChevronDown size={10} className={`transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 border-l-2 border-blue-100 pl-3">
          {sources.map((s, i) => (
            <button
              key={i}
              onClick={() => handleOpen(s)}
              className="w-full flex items-start gap-1.5 text-left group hover:bg-blue-50
                         rounded-lg px-2 py-1.5 -mx-2 transition-colors"
            >
              {iconFor(s.kind)}
              <div className="min-w-0 flex-1">
                <p className="text-[10px] font-medium text-dim group-hover:text-blue-500
                               truncate transition-colors flex items-center gap-1">
                  {s.title}
                  <ExternalLink size={8} className="opacity-0 group-hover:opacity-60 shrink-0 transition-opacity" />
                </p>
                {s.snippet && (
                  <p className="text-[9px] text-ghost leading-snug line-clamp-2">{s.snippet}</p>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function MessageBubble({ msg, onRegenerate }: { msg: Message; onRegenerate?: () => void }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[72%] px-4 py-3 rounded-2xl rounded-br-md text-sm
                     leading-relaxed text-white shadow-soft"
          style={{ background: 'linear-gradient(135deg, #3b82f6 0%, #4f46e5 100%)' }}
        >
          {msg.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3 items-start">
      {/* Avatar */}
      <div
        className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center
                   text-[12px] font-bold text-white mt-0.5 shadow-soft"
        style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
      >
        P
      </div>

      <div className="flex-1 min-w-0 space-y-2">
        {/* Agent badge — shown as soon as route event arrives */}
        {msg.agentLabel && (
          <AgentBadge
            label={msg.agentLabel}
            icon={msg.agentIcon ?? '🤖'}
            intent={msg.agentIntent}
            reason={msg.agentReason}
          />
        )}

        {/* Plan tags */}
        {msg.plan && msg.plan.length > 0 && <PlanBar steps={msg.plan} />}

        {/* Bubble */}
        <div className="relative group">
        {!msg.streaming && msg.content && <CopyButton content={msg.content} />}
        <div
          className="bg-surface rounded-2xl rounded-tl-md px-4 py-3.5 shadow-card
                     border border-line text-sm text-ink leading-relaxed"
        >
          {msg.content ? (
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          ) : msg.streaming ? (
            <span className="text-ghost text-xs flex items-center gap-1.5">
              {msg.statusText || (msg.phase === 'analyzing' ? '正在理解问题' : '思考中')}
              <ThinkingDots />
            </span>
          ) : (
            <span className="text-ghost text-xs italic">已中断</span>
          )}
          {msg.streaming && msg.content && <span className="cursor" />}
        </div>
        </div>

        {/* Sources */}
        {!msg.streaming && msg.sources && msg.sources.length > 0 && (
          <SourcesCard sources={msg.sources} />
        )}

        {/* Inline charts */}
        {!msg.streaming && msg.content && (
          <ChatChart content={msg.content} />
        )}

        {/* Timing badge + regenerate */}
        {!msg.streaming && msg.elapsedSecs !== undefined && (
          <div className="flex items-center gap-2 mt-0.5 px-1">
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                         bg-blue-50 border border-blue-100 text-blue-400
                         text-[10px] font-medium"
            >
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10"/>
                <polyline points="12 6 12 12 16 14"/>
              </svg>
              {msg.elapsedSecs}s
              {msg.phase1Secs !== undefined && msg.elapsedSecs > msg.phase1Secs && (
                <span className="opacity-70">
                  · 检索 {msg.phase1Secs}s · 生成 {msg.elapsedSecs - msg.phase1Secs}s
                </span>
              )}
            </span>
            {onRegenerate && (
              <button
                onClick={onRegenerate}
                title="重新生成"
                className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                           text-ghost hover:text-ink border border-transparent
                           hover:border-line hover:bg-muted transition-all"
              >
                <RefreshCw size={9} />
                重新生成
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
