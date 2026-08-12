const BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8001'

export type SseEvent =
  | { type: 'start' }
  | { type: 'route'; agent: string; icon: string; intent: string; reason: string }
  | { type: 'plan'; plan: PlanStep[]; tool_names: string[] }
  | { type: 'progress'; phase: string; message: string }
  | { type: 'token'; content: string }
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

export function streamChat(
  question: string,
  sessionId: string | null,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId }),
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
