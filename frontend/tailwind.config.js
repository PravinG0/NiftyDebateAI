/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: {
          DEFAULT: '#080C14',
          50: '#1e283d',
          100: '#182133',
          200: '#141C2E',
          300: '#0F1624',
          400: '#0C111C',
          500: '#080C14',
          card: '#0D131F',
          surface: '#111827',
        },
        bull: {
          DEFAULT: '#10B981',
          light: '#34D399',
          dark: '#059669',
          glow: 'rgba(16, 185, 129, 0.15)',
        },
        bear: {
          DEFAULT: '#F43F5E',
          light: '#FB7185',
          dark: '#E11D48',
          glow: 'rgba(244, 63, 94, 0.15)',
        },
        judge: {
          DEFAULT: '#F59E0B',
          light: '#FBBF24',
          dark: '#D97706',
          glow: 'rgba(245, 158, 11, 0.15)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Menlo', 'Monaco', 'Courier New', 'monospace'],
      },
      boxShadow: {
        'glass': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
        'bull-glow': '0 0 20px -3px rgba(16, 185, 129, 0.3)',
        'bear-glow': '0 0 20px -3px rgba(244, 63, 94, 0.3)',
        'judge-glow': '0 0 20px -3px rgba(245, 158, 11, 0.3)',
      },
    },
  },
  plugins: [],
};

