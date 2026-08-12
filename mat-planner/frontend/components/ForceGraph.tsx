'use client'
/**
 * Interactive force-directed graph using D3.
 * - Canvas rendering for performance
 * - Zoom / pan via d3-zoom
 * - Drag nodes
 * - Click node → onNodeClick callback
 * - Hover tooltip
 */
import { useEffect, useRef, useCallback } from 'react'
import type { GraphNode, GraphEdge } from '@/lib/api'

// Node type colours (fill, stroke)
const NODE_FILL: Record<string, string> = {
  compound:  '#38bdf8',
  property:  '#2dd4bf',
  concept:   '#a78bfa',
  process:   '#fb923c',
}
const NODE_STROKE: Record<string, string> = {
  compound:  '#0284c7',
  property:  '#0d9488',
  concept:   '#7c3aed',
  process:   '#ea580c',
}

function nodeFill(t: string)   { return NODE_FILL[t]   ?? '#94a3b8' }
function nodeStroke(t: string) { return NODE_STROKE[t] ?? '#64748b' }

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focusLabel?: string | null
  onNodeClick?: (node: GraphNode) => void
  height?: number
}

export default function ForceGraph({ nodes, edges, focusLabel, onNodeClick, height = 480 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const simRef    = useRef<import('d3').Simulation<SimNode, SimEdge> | null>(null)
  const zoomRef   = useRef<import('d3').ZoomBehavior<HTMLCanvasElement, unknown> | null>(null)
  const transformRef = useRef<import('d3').ZoomTransform | null>(null)
  const hoveredRef   = useRef<SimNode | null>(null)
  const draggedRef   = useRef<SimNode | null>(null)
  const rafRef       = useRef<number | null>(null)

  interface SimNode extends GraphNode {
    x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null
  }
  interface SimEdge extends GraphEdge {
    source: SimNode | string
    target: SimNode | string
  }

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const w = canvas.width
    const h = canvas.height
    ctx.clearRect(0, 0, w, h)
    const t = transformRef.current
    if (!t) return

    ctx.save()
    ctx.translate(t.x, t.y)
    ctx.scale(t.k, t.k)

    const sim = simRef.current
    if (!sim) { ctx.restore(); return }
    const simNodes = sim.nodes() as SimNode[]
    const nodeMap = new Map(simNodes.map(n => [n.id, n]))

    // Draw edges
    ctx.lineWidth = 1 / t.k
    ;(sim as unknown as { force: (n: string) => { links: () => SimEdge[] } }).force('link')?.links?.().forEach((e: SimEdge) => {
      const src = typeof e.source === 'string' ? nodeMap.get(e.source) : e.source as SimNode
      const tgt = typeof e.target === 'string' ? nodeMap.get(e.target) : e.target as SimNode
      if (!src?.x || !src?.y || !tgt?.x || !tgt?.y) return
      const opacity = Math.min(1, e.weight * 0.5 + 0.2)
      ctx.strokeStyle = `rgba(148,163,184,${opacity})`
      ctx.beginPath()
      ctx.moveTo(src.x, src.y)
      ctx.lineTo(tgt.x, tgt.y)
      ctx.stroke()
    })

    // Draw nodes
    simNodes.forEach(n => {
      if (!n.x || !n.y) return
      const r = n.label === focusLabel ? 10 : 7
      const hovered = hoveredRef.current?.id === n.id
      ctx.beginPath()
      ctx.arc(n.x, n.y, hovered ? r + 2 : r, 0, Math.PI * 2)
      ctx.fillStyle = nodeFill(n.node_type)
      ctx.fill()
      ctx.strokeStyle = nodeStroke(n.node_type)
      ctx.lineWidth = (hovered || n.label === focusLabel ? 2.5 : 1.5) / t.k
      ctx.stroke()

      // Label for focused / hovered / large zoom
      if (n.label === focusLabel || hovered || t.k > 1.5) {
        const label = n.label.length > 18 ? n.label.slice(0, 16) + '…' : n.label
        ctx.fillStyle = '#1e293b'
        ctx.font = `${Math.min(12, 10 / t.k + 2)}px -apple-system, sans-serif`
        ctx.textAlign = 'center'
        ctx.fillText(label, n.x, n.y + r + 11 / t.k)
      }
    })

    ctx.restore()
  }, [focusLabel])

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!nodes.length) return

    // Dynamic import D3 (large module; only client-side)
    let cancelled = false
    ;(async () => {
      const d3 = await import('d3')
      if (cancelled) return
      const canvas = canvasRef.current
      if (!canvas) return

      const w = canvas.offsetWidth
      const h = height
      canvas.width  = w * window.devicePixelRatio
      canvas.height = h * window.devicePixelRatio
      canvas.style.width  = w + 'px'
      canvas.style.height = h + 'px'
      const ctx = canvas.getContext('2d')!
      ctx.scale(window.devicePixelRatio, window.devicePixelRatio)

      // Build simulation data
      const simNodes: SimNode[] = nodes.map(n => ({ ...n }))
      const nodeById = new Map(simNodes.map(n => [n.id, n]))
      const simEdges: SimEdge[] = edges
        .map(e => ({ ...e, source: nodeById.get(e.source_id) ?? e.source_id, target: nodeById.get(e.target_id) ?? e.target_id }))
        .filter(e => typeof e.source !== 'string' && typeof e.target !== 'string')

      // Stop previous sim
      simRef.current?.stop()

      const sim = d3.forceSimulation<SimNode>(simNodes)
        .force('link', d3.forceLink<SimNode, SimEdge>(simEdges).id(n => n.id).distance(80).strength(0.3))
        .force('charge', d3.forceManyBody().strength(-120))
        .force('center', d3.forceCenter(w / 2, h / 2))
        .force('collision', d3.forceCollide(14))
        .alphaDecay(0.03)
        .on('tick', () => {
          if (rafRef.current) cancelAnimationFrame(rafRef.current)
          rafRef.current = requestAnimationFrame(draw)
        })
      simRef.current = sim as unknown as import('d3').Simulation<SimNode, SimEdge>

      // Set initial transform
      const initialT = d3.zoomIdentity
      transformRef.current = initialT

      // Zoom behaviour
      const zoom = d3.zoom<HTMLCanvasElement, unknown>()
        .scaleExtent([0.3, 6])
        .on('zoom', (event) => {
          transformRef.current = event.transform
          if (rafRef.current) cancelAnimationFrame(rafRef.current)
          rafRef.current = requestAnimationFrame(draw)
        })
      // Cast canvas to EventTarget type accepted by d3
      d3.select(canvas as unknown as Element).call(zoom as unknown as (sel: import('d3').Selection<Element, unknown, null, undefined>) => void)
      zoomRef.current = zoom as unknown as import('d3').ZoomBehavior<HTMLCanvasElement, unknown>

      // Helper: find node near mouse
      const findNode = (mx: number, my: number): SimNode | null => {
        const t = transformRef.current ?? d3.zoomIdentity
        const px = (mx - t.x) / t.k
        const py = (my - t.y) / t.k
        let best: SimNode | null = null; let bestD = 14
        simNodes.forEach(n => {
          if (n.x == null || n.y == null) return
          const d = Math.hypot(n.x - px, n.y - py)
          if (d < bestD) { bestD = d; best = n }
        })
        return best
      }

      // Hover
      canvas.addEventListener('mousemove', (e) => {
        const rect = canvas.getBoundingClientRect()
        const node = findNode(e.clientX - rect.left, e.clientY - rect.top)
        if (node !== hoveredRef.current) {
          hoveredRef.current = node
          canvas.style.cursor = node ? 'pointer' : 'grab'
          if (rafRef.current) cancelAnimationFrame(rafRef.current)
          rafRef.current = requestAnimationFrame(draw)
        }
      })

      canvas.addEventListener('mouseleave', () => {
        hoveredRef.current = null
        if (rafRef.current) cancelAnimationFrame(rafRef.current)
        rafRef.current = requestAnimationFrame(draw)
      })

      // Click → select
      canvas.addEventListener('click', (e) => {
        const rect = canvas.getBoundingClientRect()
        const node = findNode(e.clientX - rect.left, e.clientY - rect.top)
        if (node && onNodeClick) onNodeClick(node)
      })

      // Drag
      const drag = d3.drag<HTMLCanvasElement, unknown>()
        .subject((event) => {
          const rect = canvas.getBoundingClientRect()
          return findNode(event.x - rect.left, event.y - rect.top) ?? { x: 0, y: 0 }
        })
        .on('start', (event) => {
          const n = event.subject as SimNode
          if (!n || !n.id) return
          if (!event.active) sim.alphaTarget(0.3).restart()
          n.fx = n.x; n.fy = n.y
          draggedRef.current = n
        })
        .on('drag', (event) => {
          const n = draggedRef.current
          if (!n) return
          const t = transformRef.current ?? d3.zoomIdentity
          n.fx = (event.x) / t.k + (transformRef.current?.invertX(event.x) ?? event.x) - (event.x) / t.k
          // Simpler: just use raw coords adjusted by zoom
          const rect = canvas.getBoundingClientRect()
          const tx = transformRef.current ?? d3.zoomIdentity
          n.fx = (event.sourceEvent.clientX - rect.left - tx.x) / tx.k
          n.fy = (event.sourceEvent.clientY - rect.top  - tx.y) / tx.k
        })
        .on('end', (event) => {
          const n = draggedRef.current
          if (!n) return
          if (!event.active) sim.alphaTarget(0)
          n.fx = null; n.fy = null
          draggedRef.current = null
        })
      d3.select(canvas as unknown as Element).call(drag as unknown as (sel: import('d3').Selection<Element, unknown, null, undefined>) => void)
    })()

    return () => {
      cancelled = true
      simRef.current?.stop()
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, height])

  return (
    <canvas
      ref={canvasRef}
      className="w-full rounded-xl bg-app"
      style={{ height, cursor: 'grab', display: 'block' }}
    />
  )
}
