'use client'
import { useEffect, useState, useMemo, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  ArrowLeft, BookOpen, Search, Trash2, ChevronDown, ChevronRight,
  FileText, CheckCircle2, XCircle, Loader2, Clock, RefreshCw, RotateCcw,
} from 'lucide-react'
import { fetchDocuments, deleteDocument, retryDocument, type DocumentRecord } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

function fileTypeIcon(ft: string) {
  const map: Record<string, string> = {
    pdf: '📄', md: '📝', txt: '📃', docx: '📋', rst: '📄', tex: '📄', html: '🌐',
  }
  return map[ft.toLowerCase()] ?? '📄'
}

function statusBadge(status: string) {
  const map: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
    done:       { label: '已完成', cls: 'bg-green-50 text-green-600 border-green-100',  icon: <CheckCircle2 size={10} /> },
    processing: { label: '处理中', cls: 'bg-blue-50 text-blue-500 border-blue-100',    icon: <Loader2 size={10} className="animate-spin" /> },
    pending:    { label: '等待中', cls: 'bg-gray-50 text-gray-400 border-gray-100',    icon: <Clock size={10} /> },
    failed:     { label: '失败',   cls: 'bg-red-50 text-red-400 border-red-100',       icon: <XCircle size={10} /> },
  }
  const { label, cls, icon } = map[status] ?? { label: status, cls: 'bg-gray-50 text-gray-400 border-gray-100', icon: null }
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${cls}`}>
      {icon}{label}
    </span>
  )
}

function timeLabel(iso: string) {
  return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric' })
}

function DocRow({ doc, onDelete }: { doc: DocumentRecord; onDelete: (id: string) => void }) {
  const [expanded, setExpanded]         = useState(false)
  const [deleting, setDeleting]         = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [retrying, setRetrying]         = useState(false)
  const [retryDone, setRetryDone]       = useState(false)

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirmDelete) { setConfirmDelete(true); return }
    setDeleting(true)
    const ok = await deleteDocument(doc.id)
    if (ok) onDelete(doc.id)
    else setDeleting(false)
  }

  const filename = doc.source_path.split('/').pop() ?? doc.source_path

  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden
                    hover:border-blue-200 transition-all duration-200">
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded(e => !e)}
      >
        <span className="text-lg shrink-0">{fileTypeIcon(doc.file_type)}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-ink truncate">{doc.title}</span>
            {statusBadge(doc.status)}
            <span className="text-[10px] text-ghost bg-muted px-1.5 py-0.5 rounded-md">
              {doc.namespace}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-[10px] text-ghost">{filename}</span>
            <span className="text-[10px] text-ghost">{doc.chunk_count} 块 · {doc.section_count} 章节</span>
            <span className="text-[10px] text-ghost">{timeLabel(doc.created_at)}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={handleDelete}
            disabled={deleting}
            title={confirmDelete ? '再次点击确认删除' : '删除文档'}
            className={`p-1.5 rounded-lg text-xs font-medium border transition-all duration-150
                        ${confirmDelete
                          ? 'bg-red-50 border-red-200 text-red-500'
                          : 'border-transparent text-ghost hover:text-red-400 hover:bg-red-50 hover:border-red-100'
                        }`}
          >
            {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          </button>
          <div className="text-ghost">
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </div>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-line px-4 py-3 space-y-2">
          {doc.summary ? (
            <p className="text-xs text-dim leading-relaxed">{doc.summary}</p>
          ) : (
            <p className="text-xs text-ghost italic">暂无摘要</p>
          )}
          {doc.error_message && (
            <div className="space-y-1.5">
              <p className="text-xs text-red-400 bg-red-50 rounded-lg px-3 py-2">
                错误：{doc.error_message}
              </p>
              {doc.status === 'failed' && (
                <button
                  disabled={retrying || retryDone}
                  onClick={async () => {
                    setRetrying(true)
                    const result = await retryDocument(doc.source_path, doc.namespace, doc.title)
                    setRetrying(false)
                    if (result) setRetryDone(true)
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                              border transition-all duration-150
                              ${retryDone
                                ? 'bg-green-50 border-green-200 text-green-600'
                                : 'bg-red-50 border-red-200 text-red-500 hover:bg-red-100'
                              } disabled:opacity-60`}
                >
                  {retrying
                    ? <Loader2 size={11} className="animate-spin" />
                    : <RotateCcw size={11} />
                  }
                  {retryDone ? '已重新提交，前往摄取页查看进度' : '重试摄取'}
                </button>
              )}
            </div>
          )}
          <div className="flex gap-4 text-[10px] text-ghost">
            <span>ID: <code className="font-mono bg-muted px-1 rounded">{doc.id.slice(0, 8)}…</code></span>
            <span>类型: {doc.file_type.toUpperCase()}</span>
            <span>更新: {timeLabel(doc.updated_at)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function DocumentsPage() {
  const router       = useRouter()
  const searchParams = useSearchParams()
  const [docs, setDocs]         = useState<DocumentRecord[]>([])
  const [loading, setLoading]   = useState(true)
  const [query, setQuery]       = useState(() => searchParams.get('q') ?? '')
  const highlightId             = searchParams.get('id')
  const [nsFilter, setNsFilter] = useState('')
  const [sortBy, setSortBy]     = useState<'updated_desc' | 'updated_asc' | 'status' | 'title'>('updated_desc')

  useEffect(() => { document.title = '文档管理 — PyroPlanner' }, [])

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchDocuments('__all__', 200)
    setDocs(data)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const namespaces = useMemo(
    () => Array.from(new Set(docs.map(d => d.namespace))).sort(),
    [docs],
  )

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim()
    const STATUS_ORDER: Record<string, number> = { processing: 0, pending: 1, failed: 2, done: 3 }
    return docs
      .filter(d => {
        // If navigated here via a doc_id link, show only that document
        if (highlightId) return d.id === highlightId
        const matchQ  = !q || d.title.toLowerCase().includes(q) || d.source_path.toLowerCase().includes(q) || (d.summary ?? '').toLowerCase().includes(q)
        const matchNs = !nsFilter || d.namespace === nsFilter
        return matchQ && matchNs
      })
      .sort((a, b) => {
        if (sortBy === 'updated_desc') return b.updated_at.localeCompare(a.updated_at)
        if (sortBy === 'updated_asc')  return a.updated_at.localeCompare(b.updated_at)
        if (sortBy === 'status')       return (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
        if (sortBy === 'title')        return a.title.localeCompare(b.title, 'zh-CN')
        return 0
      })
  }, [docs, query, nsFilter, sortBy])

  const handleDelete = (id: string) => setDocs(prev => prev.filter(d => d.id !== id))

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
          <BookOpen size={14} className="text-brand" />
          <span className="font-semibold text-sm">文档管理</span>
        </div>
        <span className="text-[10px] text-ghost ml-1">{docs.length} 篇文献</span>
        {docs.length > 0 && (() => {
          const counts = { done: 0, processing: 0, pending: 0, failed: 0 }
          docs.forEach(d => { if (d.status in counts) counts[d.status as keyof typeof counts]++ })
          const parts = [
            counts.done       && `${counts.done} 完成`,
            counts.processing && `${counts.processing} 处理中`,
            counts.pending    && `${counts.pending} 等待`,
            counts.failed     && `${counts.failed} 失败`,
          ].filter(Boolean)
          return (
            <span className="text-[10px] text-ghost">
              ({parts.join(' · ')})
            </span>
          )
        })()}
        <button onClick={load} title="刷新"
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all ml-1">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
        <NavTabs />
      </header>

      {/* Filter bar */}
      <div className="shrink-0 px-6 py-3 border-b border-line bg-surface flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1 max-w-sm bg-muted rounded-xl px-3 py-2
                        border border-line focus-within:border-blue-300 transition-colors">
          <Search size={13} className="text-ghost shrink-0" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索标题、路径或摘要…"
            className="bg-transparent text-sm text-ink placeholder-ghost outline-none flex-1"
          />
        </div>
        {/* Sort */}
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value as typeof sortBy)}
          className="px-2.5 py-1.5 rounded-lg border border-line bg-surface text-xs text-dim
                     focus:outline-none focus:border-blue-300 transition-colors cursor-pointer"
        >
          <option value="updated_desc">最近更新 ↓</option>
          <option value="updated_asc">最近更新 ↑</option>
          <option value="status">按状态</option>
          <option value="title">按标题</option>
        </select>
        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={() => setNsFilter('')}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all
              ${!nsFilter ? 'bg-blue-50 border-blue-200 text-blue-500' : 'border-line text-ghost hover:border-blue-200 hover:text-ink'}`}>
            全部命名空间
          </button>
          {namespaces.map(ns => (
            <button key={ns} onClick={() => setNsFilter(ns === nsFilter ? '' : ns)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all
                ${nsFilter === ns ? 'bg-blue-50 border-blue-200 text-blue-500' : 'border-line text-ghost hover:border-blue-200 hover:text-ink'}`}>
              {ns}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2 text-ghost">
            <div className="w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">加载中…</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-ghost">
            <FileText size={32} className="text-blue-100" />
            <p className="text-sm">{highlightId ? '找不到该文献（可能已被删除）' : query ? `没有匹配"${query}"的文献` : '知识库暂无文献'}</p>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto space-y-2 pb-6">
            {filtered.map(doc => (
              <div key={doc.id} className={highlightId === doc.id ? 'ring-2 ring-blue-300 ring-offset-2 rounded-2xl' : ''}>
                <DocRow doc={doc} onDelete={handleDelete} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
