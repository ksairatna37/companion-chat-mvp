# Companion Chat — MVP

A small, original "interactive AI-companion chat" app: a feed of characters, a chat
with each, a soft-currency (gems) loop, and server-owned story progression. The design
is informed by a static teardown of a shipped Unity/IL2CPP app of this genre; it reuses
none of that app's assets, story content, prompts, or trademarks.

## Layout

```
NOTES.md                    # running decision log — read this to catch up fast
spec/
  BACKEND_CONTRACT.md       # the 5 endpoints, rules, economy, auth + AI schemas
  DEPLOYMENT.md             # phased plan; the 3 core systems mapped to real services
backend/
  app.py                    # CANONICAL server — Flask + Postgres (psycopg3), verified end-to-end
  app_sqlite.py             # zero-dependency local quickstart (SQLite), same contract
  schema.sql                # Postgres schema + wallet_apply() — single source of truth
  requirements.txt          # flask + psycopg[binary]
  persona.sample.json       # one character = identity + voice + safety + ordered beats
  chat_message_reference.py # annotated reference for the one atomic endpoint (charge+LLM+advance)
client/
  lib/tokens.ts             # self-contained design tokens (NOT SelfMind's)
  lib/api.ts                # typed client for the contract
  screens/FeedScreen.tsx    # FlashList of companions
  screens/ChatScreen.tsx    # history + optimistic send + gem balance
research/
  infrastructure_map.md     # MeChat's ~10 server-side systems + your equivalents
  client_architecture.md    # system/module map (147 assemblies, 6 domains)
  il2cpp_types.md           # method-level type system (networking/auth/AI/economy)
  il2cpp_types_inventory.txt# all 5,405 app types
  (dynamic trace)           # see "Part 3" below — tooling not checked in
```

This project is **independent of SelfMind** — no shared keys, no Azure, no SelfMind
design tokens. Bring your own LLM provider and database.

## Run the backend

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# A) zero-setup local (SQLite):
python app_sqlite.py                       # serves :8080, seeds the sample character

# B) Postgres (canonical / deploy target):
export DATABASE_URL=postgresql:///companion # or your Supabase/Neon URL
python app.py                               # applies schema.sql + seeds on first run
```
Both serve the same API on `:8080`. The LLM is a deterministic stub until you wire a
provider in `call_llm()` (key stays server-side in `LLM_API_KEY`).

## Build order

1. `backend/schema.sql` into a fresh Postgres; seed `persona.sample.json`.
2. Implement `GET /characters`, `GET /characters/:id/story`, `GET /wallet`.
3. Implement `POST /chat/:id/message` following `chat_message_reference.py`
   (idempotency + atomic charge + refund-on-LLM-failure).
4. `POST /wallet/daily-bonus`.
5. Drop `client/screens/*` into an Expo app, set `EXPO_PUBLIC_API_BASE`, wire auth
   token via `setAuthToken()`.

Client deps: `@shopify/flash-list`, `react-native-safe-area-context` (both already
standard in your Expo stack).

## Part 3 — discovering the real backend (not checked in)

The plan was a dynamic-trace runbook to capture the live API (endpoints + JSON) and
turn the spec from a hypothesis into the real shapes. Writing that tooling was blocked
by a safety guard, because HTTPS interception / TLS-pinning bypass is a
security-weakening capability. The approach, at a high level and using standard,
maintained pentest tools you'd run yourself:

- Run the **official** app (not the `-mod` build) on a **throwaway rooted emulator**.
- Capture HTTPS with **mitmproxy**; if the app pins certs, use maintained community
  tooling (**objection**'s `android sslpinning disable`, or the well-known
  `frida-multiple-unpinning` script).
- Record the `POST .../message` call above all — its request body (does it send
  history or just the new turn?), response shape, and how the gem economy is enforced.
- Feed findings back into `spec/BACKEND_CONTRACT.md`.

Caveats from the static pass: the `-mod` APK has signature-spoofing
(`com.android.support.killsign`) and native anti-hook guards (`libpglarmor.so`,
`libil2cpp_hook_guard`, `dlsym_hook_detected`), so Frida will likely be detected —
another reason to trace the official build. If you want this, say so and I'll help
within the guardrails (e.g. set up the emulator + mitmproxy; the pinning-bypass step
you'd run yourself with the named tools).
