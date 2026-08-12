'use client'
import type { PlanStep } from '@/lib/api'

const TOOL_LABELS: Record<string, string> = {
  search_memory:         '检索文献',
  get_entity_card:       '物质卡',
  find_tables:           '数据表',
  compare_values:        '对比属性',
  find_related_entities: '图谱展开',
  get_formulation:       '配方',
  trace_evidence:        '溯源',
  get_source_snippet:    '原文',
  agentic_search:        '深度导航',
  design_experiment:     '设计实验',
  check_safety:          '安全审查',
  log_experiment_result: '记录结果',
}

const TOOL_ICONS: Record<string, string> = {
  search_memory:         '🔍',
  get_entity_card:       '🧪',
  find_tables:           '📊',
  compare_values:        '⚖️',
  find_related_entities: '🕸️',
  get_formulation:       '⚗️',
  trace_evidence:        '🔗',
  get_source_snippet:    '📄',
  agentic_search:        '🧭',
  design_experiment:     '🔬',
  check_safety:          '🛡️',
  log_experiment_result: '📝',
}

export default function PlanBar({ steps }: { steps: PlanStep[] }) {
  if (!steps.length) return null
  return (
    <div className="flex flex-wrap gap-1.5 mb-0.5">
      {steps.map((s) => (
        <span
          key={s.step_id}
          title={s.goal}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px]
                     bg-blue-50 border border-blue-100 text-blue-600
                     font-medium leading-none transition-all duration-150
                     hover:bg-blue-100 hover:border-blue-200 cursor-default"
        >
          <span
            className="w-4 h-4 rounded-full flex items-center justify-center text-[9px]
                       font-bold text-white shrink-0"
            style={{ background: 'linear-gradient(135deg, #38bdf8, #6366f1)' }}
          >
            {s.step_id}
          </span>
          <span>{TOOL_ICONS[s.tool] ?? '🔧'}</span>
          {TOOL_LABELS[s.tool] ?? s.tool}
        </span>
      ))}
    </div>
  )
}
