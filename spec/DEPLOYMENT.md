# Deployment plan — the 3 core systems first

Standing up the companion-chat MVP for real. The teardown showed ~10 server-side systems
(`research/infrastructure_map.md`); you need **3** to have a live product: an **API**, a
**database**, and an **LLM service**. (Auth is part of the API and already built/tested.)

All of this is a **fresh, separate** footprint — your own accounts/keys. Do **not** reuse
SelfMind's Azure, Supabase, or any SelfMind key here.

## Phase 0 — local (done ✓)
- `backend/app.py` on SQLite, stub LLM. Runs and is end-to-end tested.
- Use this for client development before any cloud spend.

## Phase 1 — minimal live MVP (the 3 core systems)

### A. Database → **Postgres** (pick one)
| Option | Why | Notes |
|---|---|---|
| **Supabase** (recommended) | you already use it; free tier; Postgres + auth + storage in one | new project, not the SelfMind one |
| **Neon** | you use it; serverless Postgres, branches | good if you want cheap scale-to-zero |
- Apply `backend/schema.sql`. That's the production schema (the SQLite build mirrors it).

### B. API → **Flask** on a small host (pick one)
| Option | Why | Notes |
|---|---|---|
| **Fly.io / Railway / Render** (recommended) | dead-simple Flask deploy, cheap, your own | one container, `gunicorn app:app` |
| **AWS ECS/App Runner** | you know AWS | heavier; fine if you want it on AWS |
| **Next.js API routes on Vercel** | if you go **web-first**, API + web client in one deploy | would port `app.py` logic to TS |
- Port the SQLite calls in `app.py` to Postgres (psycopg) — the logic is identical; only the
  driver + the `wallet_apply` function (already in `schema.sql`) change. ~an afternoon.
- Secrets (`LLM_API_KEY`, `DATABASE_URL`) via the host's env/secrets, never in the repo.

### C. LLM service → the `call_llm()` seam
- Decide the provider (deferred — see the provider question in chat). Whatever you pick, the
  key stays server-side in `LLM_API_KEY`. The client never sees it.
- Content policy is the real driver: mainstream (OpenAI/Anthropic) refuse romance/NSFW;
  OpenRouter or a self-hosted open model allow the companion genre.

### Client
- **Web-first (recommended):** fastest to ship — Next.js/Vite on Vercel, reuse the API client.
- **RN app:** the `client/` stubs + `auth.ts` are ready; wire to the Phase-1 API base.

## Phase 2 — growth (add only when the loop works)
- **Push + analytics (system 10):** your own Firebase project (FCM + Analytics) or OneSignal;
  one analytics tool (Amplitude/PostHog). Add early-ish for retention insight.
- **Content CDN + pipeline (system 4):** when characters/stories outgrow JSON rows — object
  storage + a CDN + a publish step. (Supabase Storage / Cloudflare R2.)
- **Remote config / flags (system 8):** Firebase Remote Config or your own, for experiments.
- **IAP (system 5):** real Apple/Google receipt validation when you monetize.
- **Rate-limiting (system 6):** basic abuse protection; your server-authoritative wallet
  already removes most of what their integrity service is for.

## One-line recommendation
**Supabase (Postgres) + Flask on Fly.io + an OpenRouter key**, web client on Vercel. That's
the whole live MVP on three services you can stand up in an afternoon, all freshly yours.
