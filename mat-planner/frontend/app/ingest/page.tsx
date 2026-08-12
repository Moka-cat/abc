'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Upload, FileText, CheckCircle2, XCircle, Loader2, Clock, Search } from 'lucide-react'
import { fetchJobs, uploadFile, watchJob, type IngestJob } from '@/lib/api'
import NavTabs from '@/components/NavTabs'

const ACCEPTED = '.pdf,.md,.txt,.docx,.rst,.tex,.html'

function statusBadge(status: IngestJob['status']) {
  const map: Record<string, { label: string; cls: string }> = {
    pending:    { label: '等待中',  cls: 'bg-gray-100 text-gray-500' },
    processing: { label: '处理中',  cls: 'bg-blue-50 text-blue-500' },
    done:       { label: '完成',    cls: 'bg-green-50 text-green-600' },
    failed:     { label: '失败',    cls: 'bg-red-50 text-red-500' },
  }
  const { label, cls } = map[status] ?? { label: status, cls: 'bg-gray-100 text-gray-400' }
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${cls}`}>
      {label}
    </span>
  )
}

function statusIcon(status: IngestJob['status']) {
  if (status === 'done')       return <CheckCircle2 size={14} className="text-green-500 shrink-0" />
  if (status === 'failed')     return <XCircle      size={14} className="text-red-400 shrink-0" />
  if (status === 'processing') return <Loader2      size={14} className="text-blue-400 shrink-0 animate-spin" />
  return <Clock size={14} className="text-gray-300 shrink-0" />
}

function timeLabel(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fileName(path: string) {
  return path.split('/').pop() ?? path
}

export default function IngestPage() {
  const router   = useRouter()
  const [jobs, setJobs]             = useState<IngestJob[]>([])
  const [dragging, setDragging]     = useState(false)
  const [uploading, setUploading]   = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [namespace, setNamespace]   = useState('default')
  const [force,     setForce]       = useState(false)
  const inputRef  = useRef<HTMLInputElement>(null)
  // Track open WS cleanups by job_id
  const wsCleanups = useRef<Map<string, () => void>>(new Map())

  const refresh = useCallback(async () => {
    const data = await fetchJobs()
    setJobs(data)
    return data
  }, [])

  /** Open a WS for a single job if not already watching. */
  const startWatch = useCallback((jobId: string) => {
    if (wsCleanups.current.has(jobId)) return
    const cleanup = watchJob(
      jobId,
      (update) => {
        setJobs(prev => prev.map(j =>
          j.job_id === update.job_id
            ? {
                ...j,
                status:      update.status as IngestJob['status'],
                progress:    update.progress,
                message:     update.message,
                error:       update.error,
                document_id: update.document_id,
              }
            : j,
        ))
      },
      () => {
        // On terminal: refresh the full list once to sync any metadata
        wsCleanups.current.delete(jobId)
        refresh()
      },
    )
    wsCleanups.current.set(jobId, cleanup)
  }, [refresh])

  useEffect(() => { document.title = '文献摄取 — PyroPlanner' }, [])

  // On mount: load jobs, then open WS for any active ones
  useEffect(() => {
    refresh().then(data => {
      for (const job of data) {
        if (job.status === 'pending' || job.status === 'processing') {
          startWatch(job.job_id)
        }
      }
    })
    return () => {
      // Close all open WebSockets on unmount
      for (const cleanup of wsCleanups.current.values()) cleanup()
      wsCleanups.current.clear()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const MAX_FILE_MB = 50

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files)
    if (!arr.length) return

    // File size check
    const oversized = arr.filter(f => f.size > MAX_FILE_MB * 1024 * 1024)
    if (oversized.length > 0) {
      setUploadError(
        `以下文件超过 ${MAX_FILE_MB} MB 限制，无法上传：${oversized.map(f => f.name).join('、')}`
      )
      return
    }

    setUploading(true)
    setUploadError('')
    try {
      for (const f of arr) {
        const result = await uploadFile(f, namespace, force)
        // Optimistically add the new job to the list
        setJobs(prev => [{
          job_id:      result.job_id,
          status:      result.status as IngestJob['status'],
          path:        result.path,
          namespace,
          title:       f.name,
          created_at:  new Date().toISOString(),
          updated_at:  new Date().toISOString(),
          document_id: null,
          error:       null,
          progress:    0,
          message:     null,
        }, ...prev])
        // Start WS for real-time updates
        startWatch(result.job_id)
      }
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }, [namespace, startWatch])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }, [handleFiles])

  const [jobSearch, setJobSearch] = useState('')

  const matchJob = (j: IngestJob) => {
    const q = jobSearch.trim().toLowerCase()
    if (!q) return true
    return (j.title ?? fileName(j.path)).toLowerCase().includes(q)
  }

  const activeJobs = jobs.filter(j => (j.status === 'processing' || j.status === 'pending') && matchJob(j))
  const doneJobs   = jobs.filter(j => (j.status === 'done' || j.status === 'failed') && matchJob(j))

  return (
    <div className="flex flex-col h-screen bg-app text-ink">
      {/* Header */}
      <header
        className="flex items-center gap-3 px-6 py-3.5 border-b border-line
                   bg-surface/80 backdrop-blur-md shrink-0"
        style={{ boxShadow: '0 1px 0 0 #e2eaf7' }}
      >
        <button
          onClick={() => router.push('/')}
          className="p-1.5 rounded-lg text-ghost hover:text-ink hover:bg-muted transition-all"
        >
          <ArrowLeft size={15} />
        </button>
        <div className="flex items-center gap-2">
          <Upload size={14} className="text-brand" />
          <span className="font-semibold text-sm">文献摄取</span>
        </div>
        <span className="text-[10px] text-ghost ml-1">
          上传文献自动解析入库，支持 PDF / Markdown / TXT / DOCX
        </span>
        <NavTabs />
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-3xl mx-auto w-full space-y-6">

        {/* Namespace selector + force toggle */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-ghost shrink-0">知识库命名空间</label>
            <input
              value={namespace}
              onChange={e => setNamespace(e.target.value)}
              className="px-3 py-1.5 rounded-lg border border-line bg-surface text-sm
                         focus:outline-none focus:border-blue-300 transition-colors w-40"
              placeholder="default"
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setForce(f => !f)}
              className={`w-8 h-4 rounded-full transition-colors duration-200 relative shrink-0
                          ${force ? 'bg-amber-400' : 'bg-line'}`}
            >
              <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform duration-200
                               ${force ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </div>
            <span className="text-xs text-ghost">
              强制重传
              {force && <span className="ml-1 text-amber-500 font-semibold">（将覆盖已有文档）</span>}
            </span>
          </label>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-3 py-12 rounded-2xl
                      border-2 border-dashed cursor-pointer transition-all duration-200
                      ${dragging
                        ? 'border-blue-400 bg-blue-50'
                        : 'border-line bg-surface hover:border-blue-300 hover:bg-blue-50/40'
                      }`}
        >
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center shadow-soft"
            style={{ background: 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)' }}
          >
            {uploading
              ? <Loader2 size={22} className="text-white animate-spin" />
              : <Upload  size={22} className="text-white" />
            }
          </div>
          <div className="text-center">
            <p className="text-sm font-medium text-ink">
              {uploading ? '上传中…' : '拖拽文件到此处，或点击选择'}
            </p>
            <p className="text-xs text-ghost mt-0.5">
              PDF · Markdown · TXT · DOCX · RST · TEX · HTML
            </p>
          </div>
          {uploadError && (
            <p className="text-xs text-red-500 px-4 text-center">{uploadError}</p>
          )}
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED}
            className="hidden"
            onChange={e => { if (e.target.files) handleFiles(e.target.files) }}
          />
        </div>

        {/* Active jobs */}
        {activeJobs.length > 0 && (
          <section>
            <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase mb-2">
              进行中
            </h3>
            <div className="space-y-2">
              {activeJobs.map(job => (
                <JobRow key={job.job_id} job={job} />
              ))}
            </div>
          </section>
        )}

        {/* Completed jobs */}
        {(doneJobs.length > 0 || jobSearch) && (
          <section>
            <div className="flex items-center gap-3 mb-2">
              <h3 className="text-xs font-semibold text-ghost tracking-widest uppercase flex-1">
                历史记录 {jobs.filter(j => j.status === 'done' || j.status === 'failed').length > 0 &&
                  `(${jobs.filter(j => j.status === 'done' || j.status === 'failed').length})`}
              </h3>
              <div className="flex items-center gap-1.5 bg-muted rounded-lg px-2 py-1
                              border border-line focus-within:border-blue-300 transition-colors">
                <Search size={11} className="text-ghost shrink-0" />
                <input
                  value={jobSearch}
                  onChange={e => setJobSearch(e.target.value)}
                  placeholder="搜索文件名…"
                  className="bg-transparent text-[11px] text-ink placeholder-ghost outline-none w-28"
                />
              </div>
            </div>
            {doneJobs.length === 0 && jobSearch ? (
              <p className="text-xs text-ghost text-center py-4">无匹配记录</p>
            ) : (
              <div className="space-y-2">
                {doneJobs.map(job => (
                  <JobRow key={job.job_id} job={job} />
                ))}
              </div>
            )}
          </section>
        )}

        {jobs.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-12 text-ghost">
            <FileText size={32} className="text-blue-100" />
            <p className="text-sm">暂无摄取任务，上传文件开始吧</p>
          </div>
        )}
      </div>
    </div>
  )
}

function JobRow({ job }: { job: IngestJob }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3 rounded-xl bg-surface border border-line">
      {statusIcon(job.status)}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-ink truncate max-w-xs">
            {job.title ?? fileName(job.path)}
          </span>
          {statusBadge(job.status)}
          <span className="text-[10px] text-ghost">{job.namespace}</span>
        </div>
        {/* Progress bar */}
        {job.status === 'processing' && (
          <div className="mt-1.5 h-1 bg-blue-100 rounded-full overflow-hidden w-full max-w-sm">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${job.progress}%`,
                background: 'linear-gradient(90deg, #38bdf8, #6366f1)',
              }}
            />
          </div>
        )}
        {job.message && (
          <p className="text-[10px] text-ghost mt-0.5 truncate">{job.message}</p>
        )}
        {job.error && (
          <p className="text-[10px] text-red-400 mt-0.5 truncate">{job.error}</p>
        )}
      </div>
      <span className="text-[10px] text-ghost shrink-0 mt-0.5">
        {timeLabel(job.created_at)}
      </span>
    </div>
  )
}
