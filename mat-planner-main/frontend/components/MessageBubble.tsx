'use client'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import PlanBar from './PlanBar'
import type { PlanStep } from '@/lib/api'

export interface Message {
  role: 'user' | 'assistant'
  content: string
  plan?: PlanStep[]
  streaming?: boolean
  phase?: 'idle' | 'analyzing' | 'planning' | 'done'
  statusText?: string
  elapsedSecs?: number
  phase1Secs?: number
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1 ml-0.5">
      {[0,1,2].map(i => (
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

export default function MessageBubble({ msg }: { msg: Message }) {
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
        {msg.plan && msg.plan.length > 0 && <PlanBar steps={msg.plan} />}

        <div
          className="bg-surface rounded-2xl rounded-tl-md px-4 py-3.5 shadow-card
                     border border-line text-sm text-ink leading-relaxed"
        >
          {msg.content ? (
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          ) : (
            <span className="text-ghost text-xs flex items-center gap-1.5">
              {msg.statusText || (msg.phase === 'analyzing' ? '正在理解问题' : '思考中')}
              <ThinkingDots />
            </span>
          )}
          {msg.streaming && msg.content && <span className="cursor" />}
        </div>

        {/* Timing badge */}
        {!msg.streaming && msg.elapsedSecs !== undefined && (
          <div className="flex items-center gap-1.5 text-[10px] text-ghost mt-0.5 px-1">
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                         bg-blue-50 border border-blue-100 text-blue-400 font-medium"
            >
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
              {msg.elapsedSecs}s
              {msg.phase1Secs !== undefined && msg.elapsedSecs > msg.phase1Secs && (
                <span className="opacity-70">
                  · 检索 {msg.phase1Secs}s · 生成 {msg.elapsedSecs - msg.phase1Secs}s
                </span>
              )}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
