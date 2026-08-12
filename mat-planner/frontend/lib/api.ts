const BASE    = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000'

export async function fetchHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(3000) })
    return res.ok
  } catch {
    return false
  }
}
const WS_BASE = BASE.replace(/^http/, 'ws')

export interface MessageSource {
  title:   string
  snippet: string
  kind:    'entity' | 'chunk' | 'document'
  doc_id?: string | null
}

export type SseEvent =
  | { type: 'start' }
  | { type: 'route'; agent: string; icon: string; intent: string; reason: string }
  | { type: 'plan'; plan: PlanStep[]; tool_names: string[] }
  | { type: 'progress'; phase: string; message: string }
  | { type: 'token'; content: string }
  | { type: 'sources'; sources: MessageSource[] }
  | { type: 'done'; session_id: string; elapsed_secs?: number; phase1_secs?: number }

export interface PlanStep {
  step_id: number
  tool: string
  goal: string
  args: Record<string, unknown>
}

export interface Experiment {
  experiment_id: string
  goal: string
  namespace: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  error_message: string | null
  protocol_count: number
  result_count: number
  created_at: string
  updated_at: string
}

export interface Protocol {
  protocol_id: string
  rank: number
  safety_status: 'approved' | 'needs_review' | 'blocked' | null
  safety_summary: string
  rationale: string
  formulation: Record<string, { fraction: number; role: string }>
  predicted_properties: Record<string, { value: number; unit: string }>
  steps: Array<{ step: string; duration?: string; temperature?: string; notes?: string }>
  required_instruments: string[]
  safety_report: Record<string, unknown>
}

export async function fetchExperiments(limit = 30): Promise<Experiment[]> {
  const res = await fetch(`${BASE}/experiments?limit=${limit}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.experiments ?? []
}

export async function fetchExperiment(id: string): Promise<(Experiment & { protocols: Protocol[] }) | null> {
  const res = await fetch(`${BASE}/experiments/${id}`)
  if (!res.ok) return null
  return res.json()
}

// ── Ingestion Jobs ────────────────────────────────────────────────────────────

export interface IngestJob {
  job_id:      string
  status:      'pending' | 'processing' | 'done' | 'failed'
  path:        string
  namespace:   string
  title:       string | null
  created_at:  string
  updated_at:  string
  document_id: string | null
  error:       string | null
  progress:    number
  message:     string | null
}

export async function fetchJobs(): Promise<IngestJob[]> {
  const res = await fetch(`${BASE}/jobs`)
  if (!res.ok) return []
  return res.json()
}

export interface JobUpdate {
  job_id:      string
  status:      string
  progress:    number
  message:     string | null
  document_id: string | null
  error:       string | null
}

/** Opens a WebSocket to /jobs/{id}/ws and calls onUpdate for each message.
 *  Returns a cleanup function that closes the socket. */
export function watchJob(
  jobId: string,
  onUpdate: (update: JobUpdate) => void,
  onDone?: () => void,
): () => void {
  const ws = new WebSocket(`${WS_BASE}/jobs/${jobId}/ws`)
  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as JobUpdate
      onUpdate(data)
      if (data.status === 'done' || data.status === 'failed') {
        onDone?.()
        ws.close()
      }
    } catch { /* ignore */ }
  }
  ws.onerror = () => { onDone?.(); ws.close() }
  return () => ws.close()
}

export async function uploadFile(
  file: File,
  namespace = 'default',
  force = false,
): Promise<{ job_id: string; status: string; path: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('namespace', namespace)
  form.append('force', String(force))
  const res = await fetch(`${BASE}/jobs/upload`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `Upload failed (${res.status})`)
  }
  return res.json()
}

// ── Entities ──────────────────────────────────────────────────────────────────

export interface EntityProperty {
  property:      string
  value_text:    string | null
  value_numeric: number | null
  unit:          string | null
  condition:     Record<string, unknown> | null
}

export interface EntityCard {
  id:             string
  canonical_name: string
  entity_type:    string
  aliases:        string[]
  properties:     EntityProperty[]
}

export async function fetchEntities(namespace = 'default'): Promise<EntityCard[]> {
  const res = await fetch(`${BASE}/entities?namespace=${encodeURIComponent(namespace)}`)
  if (!res.ok) return []
  return res.json()
}

export async function fetchEntity(name: string): Promise<EntityCard | null> {
  const res = await fetch(`${BASE}/entities/${encodeURIComponent(name)}`)
  if (!res.ok) return null
  return res.json()
}

// ── Documents ─────────────────────────────────────────────────────────────────

export interface DocumentRecord {
  id:            string
  title:         string
  source_path:   string
  file_type:     string
  namespace:     string
  status:        string
  error_message: string | null
  summary:       string | null
  created_at:    string
  updated_at:    string
  chunk_count:   number
  section_count: number
}

export async function fetchDocuments(namespace = '__all__', limit = 100): Promise<DocumentRecord[]> {
  const res = await fetch(`${BASE}/documents?namespace=${encodeURIComponent(namespace)}&limit=${limit}`)
  if (!res.ok) return []
  return res.json()
}

export async function deleteDocument(id: string): Promise<boolean> {
  const res = await fetch(`${BASE}/documents/${id}`, { method: 'DELETE' })
  return res.ok
}

export async function retryDocument(
  sourcePath: string,
  namespace: string,
  title: string,
): Promise<{ job_id: string } | null> {
  const res = await fetch(`${BASE}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: sourcePath, namespace, title, force: true }),
  })
  if (!res.ok) return null
  return res.json()
}

// ── Retrieval Stats ───────────────────────────────────────────────────────────

export interface RetrievalStats {
  total_runs:                number
  avg_result_count:          number
  namespaces:                Record<string, number>
  zero_result_queries:       string[]
  top_queries:               Array<{ query: string; count: number }>
  result_count_distribution: Record<string, number>
}

export async function fetchRetrievalStats(namespace?: string): Promise<RetrievalStats | null> {
  const params = namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''
  const res = await fetch(`${BASE}/retrieval/stats${params}`)
  if (!res.ok) return null
  return res.json()
}

// ── Knowledge Graph ───────────────────────────────────────────────────────────

export interface GraphNode {
  id:         string
  node_type:  string
  label:      string
  ref_id:     string | null
  properties: Record<string, unknown> | null
}

export interface GraphEdge {
  id:           string
  source_id:    string
  target_id:    string
  source_label: string
  target_label: string
  edge_type:    string
  weight:       number
}

export interface GraphStats {
  node_count:    number
  edge_count:    number
  node_types:    Record<string, number>
  edge_types:    Record<string, number>
  top_connected: Array<{ label: string; degree: number }>
}

export async function fetchGraphStats(): Promise<GraphStats | null> {
  const res = await fetch(`${BASE}/graph/stats`)
  if (!res.ok) return null
  return res.json()
}

export async function searchGraphNodes(label: string, limit = 20): Promise<GraphNode[]> {
  const res = await fetch(`${BASE}/graph/nodes?label=${encodeURIComponent(label)}&limit=${limit}`)
  if (!res.ok) return []
  return res.json()
}

export async function fetchGraphNodes(
  opts: { nodeType?: string; offset?: number; limit?: number } = {},
): Promise<GraphNode[]> {
  const params = new URLSearchParams()
  if (opts.nodeType) params.set('node_type', opts.nodeType)
  if (opts.offset)   params.set('offset', String(opts.offset))
  params.set('limit', String(opts.limit ?? 20))
  const res = await fetch(`${BASE}/graph/nodes?${params}`)
  if (!res.ok) return []
  return res.json()
}

export async function fetchNeighbors(label: string, limit = 50): Promise<GraphEdge[]> {
  const res = await fetch(`${BASE}/graph/neighbors/${encodeURIComponent(label)}?limit=${limit}`)
  if (res.status === 404) return []
  if (!res.ok) return []
  return res.json()
}

export async function fetchGraphEdges(
  opts: { edgeType?: string; minWeight?: number; offset?: number; limit?: number } = {},
): Promise<GraphEdge[]> {
  const params = new URLSearchParams()
  if (opts.edgeType)   params.set('edge_type', opts.edgeType)
  if (opts.minWeight)  params.set('min_weight', String(opts.minWeight))
  if (opts.offset)     params.set('offset', String(opts.offset))
  params.set('limit', String(opts.limit ?? 200))
  const res = await fetch(`${BASE}/graph/edges?${params}`)
  if (!res.ok) return []
  return res.json()
}

// ── Data Extraction ───────────────────────────────────────────────────────────

export interface ExtractedProperty {
  property:   string
  value:      string | number | null
  unit:       string | null
  condition:  Record<string, unknown> | null
  quote:      string | null
  confidence: number | null
}

export interface ExtractedEntity {
  name:        string
  entity_type: string
  aliases:     string[]
  properties:  ExtractedProperty[]
}

export interface ExtractedTable {
  caption: string
  headers: string[]
  rows:    string[][]
}

export interface ExtractionResult {
  filename:   string
  file_type:  string
  entities:   ExtractedEntity[]
  tables:     ExtractedTable[]
  preview:    string
  text_stats: { char_count: number; entity_count: number; property_count: number; table_count: number }
}

export async function extractFile(file: File): Promise<ExtractionResult> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/extract/file`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `Extraction failed (${res.status})`)
  }
  return res.json()
}

export async function rebuildGraph(): Promise<boolean> {
  const res = await fetch(`${BASE}/graph/rebuild`, { method: 'POST' })
  return res.ok
}

// ── Chat ──────────────────────────────────────────────────────────────────────

export function streamChat(
  question: string,
  sessionId: string | null,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
  namespace?: string,
): Promise<void> {
  return fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId, namespace: namespace ?? 'default' }),
    signal,
  }).then(async (res) => {
    const reader = res.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6)) as SseEvent
          onEvent(evt)
        } catch { /* ignore malformed */ }
      }
    }
  })
}
