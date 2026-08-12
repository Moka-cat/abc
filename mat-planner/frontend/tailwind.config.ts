import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        app:     '#f0f5ff',
        surface: '#ffffff',
        muted:   '#f5f8ff',
        line:    '#e2eaf7',
        ink:     '#0d1b3e',
        dim:     '#4b5c7a',
        ghost:   '#8898b5',
        brand:   '#3b7ff5',
      },
      backgroundImage: {
        'brand-gradient': 'linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)',
        'user-bubble':    'linear-gradient(135deg, #3b82f6 0%, #4f46e5 100%)',
      },
      boxShadow: {
        'soft':  '0 1px 4px 0 rgba(59,127,245,0.06), 0 4px 16px 0 rgba(59,127,245,0.08)',
        'card':  '0 1px 3px 0 rgba(59,100,200,0.05), 0 8px 24px 0 rgba(59,100,200,0.08)',
        'input': '0 2px 8px 0 rgba(59,127,245,0.08), 0 16px 40px 0 rgba(59,127,245,0.10)',
      },
    },
  },
  plugins: [],
}

export default config
