# Project notes & decision log

Running log so nothing gets lost. Append new findings/decisions at the top of the log.
Dates are absolute.

## File index
- `README.md` — project overview + run instructions
- `spec/BACKEND_CONTRACT.md` — the API contract (endpoints, economy, auth Appendix A, AI Appendix B)
- `spec/DEPLOYMENT.md` — phased deployment; the 3 core systems mapped to concrete services
- `backend/app.py` — runnable Flask+SQLite reference server (tested end-to-end)
- `backend/schema.sql` — production Postgres schema
- `backend/chat_message_reference.py` — annotated reference for the atomic chat endpoint
- `backend/persona.sample.json` — one character = identity + voice + safety + beats
- `client/lib/{api,auth,tokens}.ts`, `client/screens/{Feed,Chat}Screen.tsx` — RN client stubs
- `research/infrastructure_map.md` — MeChat's ~10 server-side systems + your equivalents
- `research/client_architecture.md` — system/module map (147 assemblies, 6 domains)
- `research/il2cpp_types.md` — method-level type system (networking/auth/AI/economy/saves)
- `research/il2cpp_types_inventory.txt` — all 5,405 app types

## Scope line (important)
This project is an **original** companion-chat app, *informed by* a static teardown of
MeChat (world.playme.mechat). It reuses **none** of MeChat's code, content, art, prompts,
trademarks, backend, or keys. We did not extract — and will not — their proprietary logic,
their live infrastructure, or their credentials. The teardown gave us architecture
understanding only.

## Decision log

### 2026-10-23 (later) — SQLite → Postgres port DONE ✓
- **`app.py` is now the Postgres server** (psycopg3, `DATABASE_URL`), the canonical/deploy
  target. **`app_sqlite.py`** kept as the zero-dependency local quickstart (still tested).
- Uses `schema.sql` as the single schema source, incl. the `wallet_apply()` plpgsql function
  for every balance change (charge/grant/refund). `init_db()` auto-applies schema + seeds on
  first run.
- **Schema fix:** `schema.sql` uses **uuid** user_ids throughout; added `users`/`sessions` with
  `users.id uuid default gen_random_uuid()` so auth aligns with the uuid columns. (The first
  port attempt failed because the app minted text `u_<hex>` ids against uuid columns.)
- **Verified end-to-end on Postgres:** auth+30 grant, characters, story, atomic charge,
  idempotent replay (no double-charge), history, daily bonus (+25, 2nd→409), drain→402,
  unauth→401, **token rotation** (refresh invalidates the old access token), and
  **ledger == balance** holds for all users. Zero server errors.
- **Local Postgres:** installed `postgresql@16` via Homebrew and started it as a brew service
  to test. It's still running. Stop with `brew services stop postgresql@16` if you don't want
  a local daemon — real deploys use Supabase/Neon per `spec/DEPLOYMENT.md`, not this.
- `requirements.txt` now includes `psycopg[binary]`.

### 2026-10-23
- **Backend verified working.** `app.py` on SQLite, stub LLM. Full flow passes: device-ID
  auth + token pair, +30 signup grant, atomic charge, idempotent replay (no double-charge),
  daily bonus (+25, 2nd claim 409), drain → 402, unauth → 401.
- **Wallet is server-authoritative** (ledger = truth, balance = cache). This is a deliberate
  improvement over MeChat, whose wallet is a client-side save with a tamper tripwire — the
  exact weakness repackaged/modded builds exploit.
- **AI request composed server-side** (Appendix B). Client sends only `{text, client_msg_id}`;
  the server owns persona_facts + the maturity flag, so neither is client-tamperable (MeChat
  sent `allow_nsfw` from the client — we don't).
- **Auth = device-ID anonymous** → access+refresh pair, refresh on 401, heartbeat. Tokens in
  secure storage (MeChat kept them in plaintext PlayerPrefs — we don't).
- **LLM provider: DEFERRED.** `call_llm()` stays on the deterministic stub until a provider is
  chosen. Must be a fresh/own key — never SelfMind's Azure. Content policy drives the choice
  (OpenAI/Anthropic refuse the companion genre; OpenRouter / self-hosted allow it).
- **Native function-body decompile: NOT done.** Blocked by 8 GB RAM (libil2cpp.so is 139 MB;
  Ghidra needs 32 GB+). Also the IP line. Not pursuing unless explicitly needed on a big box.
- **REA fit:** right tool for the Android/archive layer; has no IL2CPP decoder, so the type
  recovery was done with Il2CppDumper, not REA.

### Open items
- [ ] Wire a real model into `call_llm()` (pick provider first).
- [x] Port `app.py` SQLite → Postgres for Phase 1. (done 2026-10-23, verified)
- [ ] Build the web client (recommended over RN for speed to ship).
- [ ] Stand up Phase 1: Supabase + Flask on Fly.io + OpenRouter key (point DATABASE_URL at Supabase).
