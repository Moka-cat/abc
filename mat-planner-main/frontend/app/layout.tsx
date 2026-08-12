import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'PyroPlanner',
  description: '含能材料实验规划助手',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="h-screen overflow-hidden font-['Inter',system-ui,sans-serif]">
        {children}
      </body>
    </html>
  )
}
