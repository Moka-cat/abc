/**
 * Conversation history stored in localStorage.
 * Each entry is a saved chat session.
 */

export interface SavedMessage {
  role: 'user' | 'assistant'
  content: string
  plan?: Array<{ step_id: number; tool: string; args: Record<string, unknown> }>
  sources?: Array<{ title: string; snippet: string; kind: string; doc_id?: string | null }>
}

export interface ConversationEntry {
  session_id: string
  title: string          // first user question
  created_at: string     // ISO string
  messages: SavedMessage[]
}

const KEY = 'pyroplanner_history'
const MAX_ENTRIES = 50

function load(): ConversationEntry[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

function save(entries: ConversationEntry[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)))
  } catch { /* quota exceeded — ignore */ }
}

export function saveConversation(
  session_id: string,
  messages: SavedMessage[],
) {
  if (!messages.length) return
  const userMsg = messages.find(m => m.role === 'user')
  if (!userMsg) return

  const entry: ConversationEntry = {
    session_id,
    title: userMsg.content.slice(0, 80),
    created_at: new Date().toISOString(),
    messages: messages.map(m => ({
      role: m.role,
      content: m.content,
      plan: m.plan,
      sources: m.sources,
    })),
  }

  const existing = load().filter(e => e.session_id !== session_id)
  save([entry, ...existing])
}

export function loadHistory(): ConversationEntry[] {
  return load()
}

export function deleteConversation(session_id: string) {
  save(load().filter(e => e.session_id !== session_id))
}

export function exportAllHistory(): void {
  const entries = load()
  const blob = new Blob(
    [JSON.stringify({ exported_at: new Date().toISOString(), conversations: entries }, null, 2)],
    { type: 'application/json;charset=utf-8' },
  )
  const url = URL.createObjectURL(blob)
  const a   = document.createElement('a')
  a.href     = url
  a.download = `pyroplanner-history-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}
