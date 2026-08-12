'use client'
import { useEffect, useRef, useState, useCallback } from 'react'
import { Send, SquarePen, Flame, Zap } from 'lucide-react'
import Sidebar from '@/components/Sidebar'
import MessageBubble, { type Message } from '@/components/MessageBubble'
import { streamChat, type PlanStep } from '@/lib/api'
import { saveConversation, type ConversationEntry } from '@/lib/history'

const SUGGESTIONS = [
  { emoji: '🧪', label: '配方设计', text: '设计一个燃速大于 15 mm/s 的低感度固体推进剂方案' },
  { emoji: '📊', label: '物性查询', text: 'RDX 和 HMX 的密度和爆速分别是多少？' },
  { emoji: '🔬', label: '工艺分析', text: 'AP 粒径对燃速有什么影响？' },
  { emoji: '📁', label: '文献检索', text: '检索 HTPB 粘合剂体系的配方数据' },
]

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [historyKey, setHistoryKey] = useState(0)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<Message[]>([])

  useEffect(() => { messagesRef.current = messages }, [messages])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const send = useCallback(async (question: string) => {
    if (!question.trim() || loading) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    setLoading(true)

    setMessages(prev => [...prev, { role: 'user', content: question }])
    setMessages(prev => [...prev, { role: 'assistant', content: '', streaming: true, phase: 'idle' }])

    let planSteps: PlanStep[] = []
    abortRef.current = new AbortController()

    try {
      await streamChat(question, sessionId, (evt) => {
        if (evt.type === 'start') {
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last.role === 'assistant')
              copy[copy.length - 1] = { ...last, phase: 'analyzing', statusText: '正在理解问题' }
            return copy
          })
        } else if (evt.type === 'plan') {
          planSteps = evt.plan
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last.role === 'assistant')
              copy[copy.length - 1] = { ...last, plan: planSteps, statusText: '正在检索文献数据' }
            return copy
          })
        } else if (evt.type === 'progress') {
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last.role === 'assistant')
              copy[copy.length - 1] = { ...last, statusText: evt.message }
            return copy
          })
        } else if (evt.type === 'token') {
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last.role === 'assistant')
              copy[copy.length - 1] = { ...last, content: last.content + evt.content, statusText: undefined }
            return copy
          })
        } else if (evt.type === 'done') {
          const newSid = evt.session_id
          const elapsedSecs = evt.elapsed_secs
          const phase1Secs = evt.phase1_secs
          setSessionId(newSid)
          setSelectedSession(newSid)
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last.role === 'assistant')
              copy[copy.length - 1] = { ...last, streaming: false, elapsedSecs, phase1Secs }
            const toSave = copy.map(m => ({ role: m.role, content: m.content, plan: m.plan }))
            saveConversation(newSid, toSave)
            return copy
          })
          setHistoryKey(k => k + 1)
        }
      }, abortRef.current.signal)
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        setMessages(prev => {
          const copy = [...prev]
          const last = copy[copy.length - 1]
          if (last.role === 'assistant')
            copy[copy.length - 1] = { ...last, content: last.content || '请求出错，请重试。', streaming: false }
          return copy
        })
      }
    } finally {
      setLoading(false)
    }
  }, [loading, sessionId])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
  }

  const reset = () => {
    abortRef.current?.abort()
    setMessages([])
    setSessionId(null)
    setSelectedSession(null)
    setLoading(false)
  }

  const restoreSession = (entry: ConversationEntry) => {
    if (loading) return
    setSelectedSession(entry.session_id)
    setSessionId(entry.session_id)
    setMessages(entry.messages.map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
      plan: m.plan as Message['plan'],
      streaming: false,
    })))
  }

  const handleDeleteSession = (sid: string) => {
    if (sessionId === sid) reset()
  }

  return (
    <div className="flex h-screen bg-app text-ink overflow-hidden">
      <Sidebar
        selectedId={selectedSession}
        onSelect={restoreSession}
        onDelete={handleDeleteSession}
        refreshKey={historyKey}
      />

      <main className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header
          className="flex items-center justify-between px-6 py-3.5 border-b border-line
                     bg-surface/80 backdrop-blur-md shrink-0"
          style={{ boxShadow: '0 1px 0 0 #e2eaf7' }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-xl flex items-center justify-center shadow-soft"
              style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
            >
              <Flame size={15} className="text-white" />
            </div>
            <div className="flex flex-col">
              <span className="font-bold text-sm text-ink leading-tight">PyroPlanner</span>
              <span className="text-[10px] text-ghost leading-tight">含能材料实验规划助手</span>
            </div>
          </div>

          <button
            onClick={reset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                       text-dim hover:text-ink border border-line hover:border-blue-200
                       hover:bg-blue-50 transition-all duration-150"
          >
            <SquarePen size={12} />
            新对话
          </button>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            /* ── Empty state ── */
            <div className="flex flex-col items-center justify-center h-full gap-10 px-6">
              {/* Logo block */}
              <div className="text-center">
                <div
                  className="w-16 h-16 rounded-2xl mx-auto mb-5 flex items-center justify-center shadow-card"
                  style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
                >
                  <Flame size={32} className="text-white" />
                </div>
                <h2 className="text-2xl font-bold text-ink tracking-tight">PyroPlanner</h2>
                <p className="text-sm text-ghost mt-2 max-w-xs leading-relaxed">
                  检索含能材料文献、设计实验方案、安全门控评估
                </p>
              </div>

              {/* Suggestion cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.text}
                    onClick={() => send(s.text)}
                    className="group text-left px-4 py-3.5 rounded-xl bg-surface border border-line
                               hover:border-blue-200 hover:shadow-soft
                               transition-all duration-200 hover:-translate-y-0.5"
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-lg">{s.emoji}</span>
                      <span className="text-[10px] font-semibold text-blue-400 tracking-wide uppercase">
                        {s.label}
                      </span>
                    </div>
                    <p className="text-xs text-dim group-hover:text-ink transition-colors leading-relaxed">
                      {s.text}
                    </p>
                  </button>
                ))}
              </div>

              {/* Footer hint */}
              <div className="flex items-center gap-1.5 text-[10px] text-ghost">
                <Zap size={10} className="text-blue-300" />
                基于已收录文献作答，支持多轮对话
              </div>
            </div>
          ) : (
            /* ── Chat ── */
            <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">
              {messages.map((msg, i) => (
                <MessageBubble key={i} msg={msg} />
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {/* Input area */}
        <div className="shrink-0 px-6 pb-6 pt-3">
          <div className="max-w-3xl mx-auto">
            <div
              className="flex items-end gap-3 bg-surface rounded-2xl px-4 py-3
                         border border-line focus-within:border-blue-300
                         transition-all duration-200 shadow-input"
            >
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value)
                  e.target.style.height = 'auto'
                  e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
                }}
                onKeyDown={handleKey}
                placeholder="描述研究目标，或直接提问…"
                disabled={loading}
                className="flex-1 resize-none bg-transparent text-sm text-ink
                           placeholder-ghost outline-none leading-relaxed max-h-40"
              />
              <button
                onClick={() => send(input)}
                disabled={loading || !input.trim()}
                className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center
                           text-white disabled:opacity-40 disabled:cursor-not-allowed
                           transition-all duration-150 hover:scale-105 active:scale-95"
                style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
              >
                <Send size={13} />
              </button>
            </div>
            <p className="text-[10px] text-ghost text-center mt-2">
              本系统仅基于已收录文献作答，不构成实验安全建议
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
