import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}'
  ],
  theme: {
    extend: {
      colors: {
        panel: '#f8fafc',
        ink: '#0f172a',
        muted: '#475569',
        accent: '#0ea5e9',
        ok: '#16a34a',
        warn: '#ca8a04',
        bad: '#dc2626'
      }
    }
  },
  plugins: []
};

export default config;
