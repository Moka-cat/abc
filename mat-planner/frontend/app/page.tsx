'use client'
import { useEffect, useRef, useState, useCallback } from 'react'
import { Send, Square, SquarePen, Flame, Zap, Download, ChevronDown, Database } from 'lucide-react'
import Sidebar from '@/components/Sidebar'
import MessageBubble, { type Message } from '@/components/MessageBubble'
import { streamChat, fetchDocuments, type PlanStep, type MessageSource } from '@/lib/api'
import { saveConversation, deleteConversation, type ConversationEntry } from '@/lib/history'

function exportToMarkdown(messages: Message[], sessionId: string | null) {
  const lines: string[] = [
    '# PyroPlanner 对话记录',
    '',
    `**Session:** ${sessionId ?? '未保存'}`,
    `**导出时间:** ${new Date().toLocaleString('zh-CN')}`,
    '',
    '---',
    '',
  ]
  for (const msg of messages) {
    if (msg.role === 'user') {
      lines.push(`**用户**`, '', msg.content, '')
    } else {
      if (msg.agentLabel) {
        lines.push(`**${msg.agentIcon ?? ''} ${msg.agentLabel}**`, '')
      }
      if (msg.content) {
        lines.push(msg.content, '')
      }
      if (msg.elapsedSecs !== undefined) {
        lines.push(`> ⏱ ${msg.elapsedSecs}s`, '')
      }
    }
    lines.push('---', '')
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `pyroplanner-${sessionId ?? Date.now()}.md`
  a.click()
  URL.revokeObjectURL(url)
}

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
  const [namespace, setNamespace] = useState('default')
  const [namespaces, setNamespaces] = useState<string[]>(['default'])
  const [nsOpen, setNsOpen] = useState(false)
  const [nsChangedWarning, setNsChangedWarning]   = useState(false)
  const [restoredTitle, setRestoredTitle]         = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<Message[]>([])
  // In-memory cache: session_id → messages, so switching back avoids localStorage reload
  const sessionCacheRef = useRef<Map<string, Message[]>>(new Map())
  // Background streaming state (supports multiple parallel streams)
  const streamingSessionsRef = useRef<Set<string>>(new Set())      // all actively streaming session IDs
  const streamBuffersRef     = useRef<Map<string, Message[]>>(new Map()) // per-session live buffer
  const abortControllersRef  = useRef<Map<string, AbortController>>(new Map()) // per-session abort
  const selectedSessionRef   = useRef<string | null>(null)         // which session is displayed

  useEffect(() => { document.title = 'PyroPlanner — 含能材料实验规划助手' }, [])
  useEffect(() => { messagesRef.current = messages }, [messages])
  useEffect(() => { selectedSessionRef.current = selectedSession }, [selectedSession])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  useEffect(() => {
    fetchDocuments('__all__', 200).then(docs => {
      const ns = Array.from(new Set(docs.map(d => d.namespace))).sort()
      if (ns.length > 0) setNamespaces(ns)
    })
  }, [])

  const nsRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!nsOpen) return
    const close = (e: MouseEvent) => {
      if (nsRef.current && !nsRef.current.contains(e.target as Node)) setNsOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [nsOpen])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ctrl+K — focus input
      if (e.key === 'k' && (e.ctrlKey || e.metaKey) && !e.shiftKey) {
        e.preventDefault()
        textareaRef.current?.focus()
      }
      // Ctrl+Shift+N — new conversation
      if (e.key === 'N' && (e.ctrlKey || e.metaKey) && e.shiftKey) {
        e.preventDefault()
        reset()
        setTimeout(() => textareaRef.current?.focus(), 50)
      }
      // Escape — close namespace dropdown or blur input
      if (e.key === 'Escape') {
        if (nsOpen) { setNsOpen(false); return }
        textareaRef.current?.blur()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  // reset and nsOpen are stable refs/values; intentionally omit to avoid re-registering
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nsOpen])

  const send = useCallback(async (question: string) => {
    if (!question.trim() || loading) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    setLoading(true)

    // For new conversations, generate a temp client-side ID so conversation is
    // visible in history before the server returns a real session_id.
    const effectiveSid = sessionId ?? crypto.randomUUID()
    if (!sessionId) {
      setSessionId(effectiveSid)
      setSelectedSession(effectiveSid)
      selectedSessionRef.current = effectiveSid  // sync immediately, don't wait for useEffect
      saveConversation(effectiveSid, [{ role: 'user', content: question }])
      setHistoryKey(k => k + 1)
    }

    // Initialize per-session stream buffer
    const initBuf: Message[] = [
      ...messagesRef.current,
      { role: 'user', content: question },
      { role: 'assistant', content: '', streaming: true, phase: 'idle' as const },
    ]
    streamingSessionsRef.current.add(effectiveSid)
    streamBuffersRef.current.set(effectiveSid, initBuf)

    // Helper: update this session's buffer; sync to display only if it's currently shown
    const updateStream = (updater: (prev: Message[]) => Message[]) => {
      const cur = streamBuffersRef.current.get(effectiveSid) ?? []
      const next = updater(cur)
      streamBuffersRef.current.set(effectiveSid, next)
      if (effectiveSid === selectedSessionRef.current) {
        setMessages([...next])
      }
    }

    if (effectiveSid === selectedSessionRef.current) {
      setMessages([...initBuf])
    }

    let planSteps: PlanStep[] = []
    const controller = new AbortController()
    abortRef.current = controller
    abortControllersRef.current.set(effectiveSid, controller)

    try {
      await streamChat(question, sessionId, (evt) => {
        if (evt.type === 'start') {
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, phase: 'analyzing', statusText: '正在理解问题' }
            return copy
          })
        } else if (evt.type === 'route') {
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, agentLabel: evt.agent, agentIcon: evt.icon, agentIntent: evt.intent, agentReason: evt.reason, statusText: `${evt.icon} ${evt.agent}` }
            return copy
          })
        } else if (evt.type === 'plan') {
          planSteps = evt.plan
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, plan: planSteps, statusText: '正在检索文献数据' }
            return copy
          })
        } else if (evt.type === 'sources') {
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, sources: evt.sources as MessageSource[] }
            return copy
          })
        } else if (evt.type === 'progress') {
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, statusText: evt.message }
            return copy
          })
        } else if (evt.type === 'token') {
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, content: last.content + evt.content, statusText: undefined }
            return copy
          })
        } else if (evt.type === 'done') {
          const newSid = evt.session_id
          if (effectiveSid !== newSid) deleteConversation(effectiveSid)
          updateStream(prev => {
            const copy = [...prev]; const last = copy[copy.length - 1]
            if (last.role === 'assistant') copy[copy.length - 1] = { ...last, streaming: false, elapsedSecs: evt.elapsed_secs, phase1Secs: evt.phase1_secs }
            return copy
          })
          const finalMsgs = streamBuffersRef.current.get(effectiveSid) ?? []
          streamingSessionsRef.current.delete(effectiveSid)
          streamBuffersRef.current.delete(effectiveSid)
          abortControllersRef.current.delete(effectiveSid)
          sessionCacheRef.current.set(newSid, finalMsgs)
          saveConversation(newSid, finalMsgs.map(m => ({ role: m.role, content: m.content, plan: m.plan, sources: m.sources })))
          if (selectedSessionRef.current === effectiveSid || selectedSessionRef.current === newSid) {
            selectedSessionRef.current = newSid  // sync immediately
            setSessionId(newSid)
            setSelectedSession(newSid)
            setMessages([...finalMsgs])
          }
          setHistoryKey(k => k + 1)
        }
      }, controller.signal, namespace)
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        updateStream(prev => {
          const copy = [...prev]; const last = copy[copy.length - 1]
          if (last.role === 'assistant') copy[copy.length - 1] = { ...last, content: last.content || '请求出错，请重试。', streaming: false }
          return copy
        })
      }
      streamingSessionsRef.current.delete(effectiveSid)
      streamBuffersRef.current.delete(effectiveSid)
      abortControllersRef.current.delete(effectiveSid)
    } finally {
      // Only clear loading if the currently displayed session is no longer streaming.
      // A background stream finishing must not steal loading=false from the active session.
      if (!streamingSessionsRef.current.has(selectedSessionRef.current ?? '')) {
        setLoading(false)
      }
    }
  }, [loading, sessionId, namespace])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
  }

  const reset = () => {
    // Don't abort ongoing streams — let them finish in the background
    // Cache current display before clearing
    if (sessionId && messagesRef.current.length > 0 && !streamingSessionsRef.current.has(sessionId)) {
      sessionCacheRef.current.set(sessionId, messagesRef.current)
    }
    setMessages([])
    setSessionId(null)
    setSelectedSession(null)
    setLoading(false)
    setNsChangedWarning(false)
    setRestoredTitle(null)
  }

  const restoreSession = (entry: ConversationEntry) => {
    // Cache current display before leaving (non-streaming sessions only)
    if (sessionId && messagesRef.current.length > 0 && !streamingSessionsRef.current.has(sessionId)) {
      sessionCacheRef.current.set(sessionId, messagesRef.current)
    }

    selectedSessionRef.current = entry.session_id  // sync immediately
    setSelectedSession(entry.session_id)

    // If switching to an actively streaming session, show its live buffer
    if (streamingSessionsRef.current.has(entry.session_id)) {
      setMessages([...(streamBuffersRef.current.get(entry.session_id) ?? [])])
      setSessionId(entry.session_id)
      setLoading(true)
      setNsChangedWarning(false)
      setRestoredTitle(null)
      return
    }

    // Switching to a non-streaming session — ensure loading is cleared
    setLoading(false)

    // Otherwise restore from in-memory cache or localStorage
    const cached = sessionCacheRef.current.get(entry.session_id)
    const msgs: Message[] = cached ?? entry.messages.map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
      plan: m.plan as Message['plan'],
      sources: m.sources as Message['sources'],
      streaming: false,
    }))
    setMessages(msgs)
    setSessionId(entry.session_id)
    setNsChangedWarning(false)
    setRestoredTitle(cached ? null : entry.title)
    setTimeout(() => setRestoredTitle(null), 3000)
  }

  const handleDeleteSession = (sid: string) => {
    if (sessionId === sid) reset()
  }

  // Find the last user message and re-send it
  const regenerate = useCallback(() => {
    const lastUser = [...messagesRef.current].reverse().find(m => m.role === 'user')
    if (!lastUser || loading) return
    // Remove the last assistant message, then resend
    setMessages(prev => {
      const copy = [...prev]
      // Remove trailing assistant messages
      while (copy.length && copy[copy.length - 1].role === 'assistant') copy.pop()
      return copy
    })
    setTimeout(() => send(lastUser.content), 50)
  }, [loading, send])

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

          <div className="flex items-center gap-2">
            {/* Namespace selector */}
            <div className="relative" ref={nsRef}>
              <button
                onClick={() => setNsOpen(o => !o)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                           text-dim hover:text-ink border border-line hover:border-blue-200
                           hover:bg-blue-50 transition-all duration-150"
              >
                <Database size={11} className="text-blue-400 shrink-0" />
                <span className="max-w-[80px] truncate">{namespace}</span>
                <ChevronDown size={10} className={`transition-transform duration-150 ${nsOpen ? 'rotate-180' : ''}`} />
              </button>
              {nsOpen && (
                <div className="absolute right-0 top-full mt-1 bg-surface border border-line
                                rounded-xl shadow-card z-50 min-w-[130px] py-1 overflow-hidden">
                  {namespaces.map(ns => (
                    <button
                      key={ns}
                      onClick={() => {
                      if (ns !== namespace && messages.length > 0) setNsChangedWarning(true)
                      setNamespace(ns)
                      setNsOpen(false)
                    }}
                      className={`w-full text-left px-3 py-1.5 text-xs transition-colors
                                  ${ns === namespace
                                    ? 'text-blue-500 bg-blue-50 font-semibold'
                                    : 'text-dim hover:text-ink hover:bg-muted'
                                  }`}
                    >
                      {ns}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {messages.length > 0 && !loading && (
              <button
                onClick={() => exportToMarkdown(messages, sessionId)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                           text-dim hover:text-ink border border-line hover:border-blue-200
                           hover:bg-blue-50 transition-all duration-150"
                title="导出为 Markdown"
              >
                <Download size={12} />
                导出
              </button>
            )}
            <button
              onClick={reset}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                         text-dim hover:text-ink border border-line hover:border-blue-200
                         hover:bg-blue-50 transition-all duration-150"
            >
              <SquarePen size={12} />
              新对话
            </button>
          </div>
        </header>

        {/* Namespace-changed warning banner */}
        {nsChangedWarning && (
          <div className="shrink-0 mx-6 mt-3 px-4 py-2.5 rounded-xl
                          bg-amber-50 border border-amber-200
                          flex items-center gap-3">
            <span className="text-xs text-amber-700 flex-1">
              命名空间已切换为 <span className="font-semibold">{namespace}</span>，当前对话历史仍基于原库。建议开启新对话。
            </span>
            <button
              onClick={() => { reset(); setNsChangedWarning(false) }}
              className="text-[11px] font-semibold text-amber-700 hover:text-amber-900
                         px-2.5 py-1 rounded-lg border border-amber-300 hover:bg-amber-100
                         transition-all shrink-0"
            >
              新对话
            </button>
            <button
              onClick={() => setNsChangedWarning(false)}
              className="text-[11px] text-amber-500 hover:text-amber-700 transition-colors shrink-0"
            >
              忽略
            </button>
          </div>
        )}

        {/* Session restored banner */}
        {restoredTitle && (
          <div className="shrink-0 mx-6 mt-3 px-4 py-2 rounded-xl
                          bg-blue-50 border border-blue-200
                          flex items-center gap-2 animate-fade-in">
            <span className="text-xs text-blue-600 flex-1 truncate">
              已恢复对话：<span className="font-medium">{restoredTitle}</span>
            </span>
          </div>
        )}

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
                <MessageBubble
                  key={i}
                  msg={msg}
                  onRegenerate={
                    msg.role === 'assistant' && i === messages.length - 1 && !msg.streaming
                      ? regenerate
                      : undefined
                  }
                />
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
              {loading ? (
                <button
                  onClick={() => abortRef.current?.abort()}
                  className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center
                             text-white transition-all duration-150 hover:scale-105 active:scale-95"
                  style={{ background: 'linear-gradient(135deg, #f87171 0%, #ef4444 100%)' }}
                  title="停止生成"
                >
                  <Square size={11} fill="white" />
                </button>
              ) : (
                <button
                  onClick={() => send(input)}
                  disabled={!input.trim()}
                  className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center
                             text-white disabled:opacity-40 disabled:cursor-not-allowed
                             transition-all duration-150 hover:scale-105 active:scale-95"
                  style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
                >
                  <Send size={13} />
                </button>
              )}
            </div>
            <div className="flex items-center justify-center gap-2 mt-2">
              <span className="text-[10px] text-ghost">
                查询范围：
                <span className="text-blue-400 font-medium">{namespace}</span>
              </span>
              <span className="text-ghost opacity-40 text-[10px]">·</span>
              <span className="text-[10px] text-ghost">本系统仅基于已收录文献作答，不构成实验安全建议</span>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
