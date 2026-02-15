# OOTD App Frontend (MVP)

Next.js app-router + Tailwind dashboard for:
- Create job form (`image`, `look_count`, `quality_mode`, `tone`, `theme`)
- Job progress polling/status
- Score breakdown + evidence tags
- Category rerank action
- Human review approve action
- History list and job re-open

## Run

```bash
cd /home/junsung/vibe_coding/lookbook-app/ootd-app/frontend
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

## Env

- `NEXT_PUBLIC_API_BASE` (default fallback: `http://localhost:8000`)

## Scripts

- `npm run dev`
- `npm run build`
- `npm run start`
- `npm run lint`
- `npm run typecheck`
