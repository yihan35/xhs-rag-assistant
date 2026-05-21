/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        xhs: {
          red:    '#FF2442',
          pink:   '#FF6B9D',
          rose:   '#FFE4EA',
          light:  '#FFF5F7',
          dark:   '#CC1C36',
        }
      },
      fontFamily: {
        sans: ['"PingFang SC"', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
      },
      animation: {
        'fade-in':      'fadeIn 0.3s ease-out',
        'slide-up':     'slideUp 0.4s ease-out',
        'bounce-dot':   'bounceDot 1.2s infinite',
        'pulse-soft':   'pulseSoft 2s ease-in-out infinite',
        'cursor-blink': 'cursorBlink 0.8s ease-in-out infinite',
        'spin-status':  'spin 0.9s linear infinite',
        'source-in':    'sourceIn 0.3s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        bounceDot: {
          '0%, 80%, 100%': { transform: 'scale(0)' },
          '40%':           { transform: 'scale(1)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.5' },
        },
        cursorBlink: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0' },
        },
        sourceIn: {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      boxShadow: {
        'card':   '0 2px 12px rgba(0,0,0,0.08)',
        'card-hover': '0 8px 24px rgba(255,36,66,0.15)',
        'chat':   '0 4px 20px rgba(0,0,0,0.1)',
      }
    },
  },
  plugins: [],
}
