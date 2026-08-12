'use client'
import { useRouter, usePathname } from 'next/navigation'
import { MessageCircle, Upload, FlaskConical, BookOpen, BarChart2, GitFork, TestTube2, Microscope } from 'lucide-react'

const TABS = [
  { href: '/',            icon: MessageCircle, label: '对话' },
  { href: '/ingest',      icon: Upload,        label: '摄取' },
  { href: '/documents',   icon: BookOpen,      label: '文档' },
  { href: '/entities',    icon: FlaskConical,  label: '实体库' },
  { href: '/experiments', icon: TestTube2,     label: '实验' },
  { href: '/extract',     icon: Microscope,    label: '抽取' },
  { href: '/graph',       icon: GitFork,       label: '图谱' },
  { href: '/stats',       icon: BarChart2,     label: '统计' },
]

export default function NavTabs() {
  const router   = useRouter()
  const pathname = usePathname()

  return (
    <div className="flex items-center gap-1 ml-auto">
      {TABS.map(({ href, icon: Icon, label }) => {
        const active = pathname === href
        return (
          <button
            key={href}
            onClick={() => router.push(href)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                        border transition-all duration-150
                        ${active
                          ? 'bg-blue-50 border-blue-200 text-blue-500'
                          : 'border-line text-ghost hover:text-ink hover:border-blue-200 hover:bg-blue-50'
                        }`}
          >
            <Icon size={12} />
            {label}
          </button>
        )
      })}
    </div>
  )
}
