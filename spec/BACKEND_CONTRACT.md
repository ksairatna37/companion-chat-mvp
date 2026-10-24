# Companion Chat — Backend Contract (MVP v1)

A minimal "interactive AI-companion chat" backend. Captures the core loop seen in
the MeChat teardown — **chat + stories + a soft currency** — without the ad / offer
sprawl. Server owns personas, story beats, wallet balance, and the LLM call. The
client never holds the model key.

> This is an original design informed by the architecture of the analysed app.
> It reuses none of that app's assets, story content, prompts, or trademarks.

---

## 0. Principles

- **Server-authoritative economy.** The client never decides a balance. Every spend
  is a server transaction against a ledger. (The analysed app had `Store.Validation`
  + remote saves + a `CheaterExposing` module — client-trusted balances are the #1
  thing to not copy.)
- **Content is data, not code.** Characters and story beats are rows the server
  returns, so you add content without shipping an app update (their Addressables
  idea, done simply).
- **LLM is server-side only.** The device sends user text; the server composes the
  prompt (persona + recent history + current beat) and calls the model. Key stays
  on the server.
- **Stateless client, resumable server.** Story state and wallet live server-side and
  survive reinstall.

## 1. Auth

Reuse whatever you already run (Supabase Auth or email/OTP). Every endpoint below
requires `Authorization: Bearer <token>`; the server resolves it to `user_id`.
No new auth system for v1.

All responses are JSON. Errors use:

```json
{ "error": { "code": "insufficient_gems", "message": "Not enough gems." } }
```

Error codes used in v1: `unauthorized`, `not_found`, `insufficient_gems`,
`rate_limited`, `llm_unavailable`, `validation`.

## 2. Endpoints

### GET /v1/characters
List the companions available to this user.

```json
{
  "characters": [
    {
      "id": "aria",
      "name": "Aria",
      "tagline": "A painter who talks in colour.",
      "avatar_url": "https://cdn.example.com/char/aria/avatar.webp",
      "accent_color": "#F4A988",
      "locked": false,
      "unlock_cost_gems": 0
    }
  ]
}
```

- `locked` + `unlock_cost_gems` let you gate some characters behind the currency later.
  v1 can ship everything unlocked.

### GET /v1/characters/:id/story
The user's current story state with this character: which beat they're on, and the
beats already unlocked (for replay/scrollback).

```json
{
  "character_id": "aria",
  "current_beat": "first_meeting",
  "beats_unlocked": ["intro", "first_meeting"],
  "relationship": { "level": 2, "points": 140, "points_to_next": 60 },
  "opener": "You find Aria repainting the same wall for the third time."
}
```

- `opener` is the scene-setting line the client shows when the chat is empty.
- `relationship` is optional in v1 but cheap to include; it's the hook that makes the
  loop sticky.

### GET /v1/chat/:id/messages?before=<cursor>&limit=30
Paginated history (newest first). Mirror your existing FlashList pagination.

```json
{
  "messages": [
    { "id": "m_912", "role": "assistant", "text": "Red again. It kept lying to me.",
      "created_at": "2026-10-23T14:02:11Z", "beat": "first_meeting" },
    { "id": "m_911", "role": "user", "text": "why red?",
      "created_at": "2026-10-23T14:02:02Z" }
  ],
  "next_cursor": "m_890"
}
```

### POST /v1/chat/:id/message
The core call. Send one user message, get the companion's reply. **This is where the
wallet is checked and decremented, the LLM is called, and story state advances —
atomically.**

Request:

```json
{ "text": "why do you keep painting it red?", "client_msg_id": "uuid-for-idempotency" }
```

Response (200):

```json
{
  "user_message":      { "id": "m_911", "text": "why do you keep painting it red?" },
  "assistant_message": { "id": "m_912", "text": "Red again. It kept lying to me.",
                         "beat": "first_meeting" },
  "wallet":   { "gems": 47 },
  "story":    { "current_beat": "first_meeting", "advanced": false,
                "relationship": { "level": 2, "points": 145, "points_to_next": 55 } }
}
```

Rules:
- **Idempotency:** `client_msg_id` is a client-generated UUID. A retry with the same
  id returns the same result and does **not** double-charge. (The analysed app's AI
  layer retried on `ConnectionError` — you must dedupe or you double-bill.)
- **Spend:** costs `GEMS_PER_MESSAGE` (config, e.g. 1). If `gems < cost` →
  `402` + `insufficient_gems`, nothing is charged, no LLM call.
- **Order of operations:** validate → check wallet → **reserve/charge in a DB
  transaction** → call LLM → on LLM success commit + persist both messages + advance
  story; on LLM failure refund the reservation and return `503 llm_unavailable`.
- **Story advance:** server decides if this exchange moves the beat (simple v1 rule:
  advance after N exchanges in a beat, or when the model emits a sentinel — see §4).

### GET /v1/wallet
```json
{ "gems": 47, "daily_bonus_available": true, "next_bonus_at": "2026-10-24T00:00:00Z" }
```

### POST /v1/wallet/daily-bonus
Grants the once-per-day free top-up. Idempotent per UTC day.

```json
{ "granted": 25, "gems": 72, "next_bonus_at": "2026-10-24T00:00:00Z" }
```

(A cron can also pre-grant, but an on-demand claim is simpler and needs no scheduler.)

## 3. Data model (Postgres)

See `schema.sql`. Tables:

| table          | holds |
|----------------|-------|
| `characters`   | persona catalog (content, editable without an app release) |
| `story_beats`  | ordered beats per character, with the model guidance for each |
| `user_story`   | per (user, character): current beat, unlocked beats, relationship |
| `messages`     | full chat log (role, text, beat, client_msg_id for idempotency) |
| `wallet`       | per-user gem balance + last daily-bonus date |
| `wallet_ledger`| append-only record of every grant/spend (audit + anti-cheat) |

The `wallet` balance is a cache; `wallet_ledger` is the source of truth. Reconstruct
balance = `SUM(ledger.delta)`; the `wallet.gems` column is just a fast read you keep
in sync inside the same transaction.

## 4. Persona + story shape

See `persona.sample.json`. One character = identity + voice + safety rails + an
ordered list of beats. A beat carries the *situation* and *goal* you inject into the
prompt, plus an `advance_when` rule the server evaluates.

The server builds each LLM call as:

```
system  = persona.system + voice + safety + current_beat.guidance
history = last N turns (role/text)
user    = <the new user text>
```

`advance_when` options for v1 (keep it dumb):
- `{ "type": "exchanges", "count": 6 }` — advance after 6 user turns in this beat.
- `{ "type": "model_signal" }` — the beat guidance tells the model to end its reply
  with a hidden sentinel like `[[beat_complete]]`; the server strips it, and advances.

## 5. Config (env, server-side only)

```
LLM_PROVIDER=         # your own provider — NOT a SelfMind key
LLM_MODEL=
LLM_API_KEY=
GEMS_PER_MESSAGE=1
DAILY_BONUS_GEMS=25
HISTORY_TURNS=12      # how many prior turns to feed the model
```

## 6. Explicitly out of scope for v1

Ad mediation, the dozen offer types, VIP tiers, merch, push, adult-content gating,
anti-cheat beyond the ledger. Add monetization only once the loop is fun.

## 6b. What the teardown confirmed (and one place we go further)

The native type recovery (`research/il2cpp_types.md`) showed the analysed app's real
design. Most of it validates this spec; one difference is deliberate and in our favour:

- **Auth:** they use **device-ID anonymous auth** → an access+refresh token pair, bearer
  header, silent refresh on 401, plus a heartbeat. A good, low-friction default — worth
  copying over a password wall for v1. (They store tokens in PlayerPrefs in plaintext;
  put yours in secure storage.)
- **AI call:** their request payload is essentially `{user_profile, bot_profile{name,
  persona_facts}, context[]{turn,message}, chat_settings{allow_nsfw}}` — i.e. persona +
  recent turns + a per-call content flag, composed server-side. That's the same shape as
  §2's `POST /chat/:id/message`; `persona.sample.json` already matches it.
- **Economy — the divergence:** their gem **wallet is a client-side local save** with a
  `FakeSpentDetected` tamper tripwire; the server is authoritative only for **IAP receipt
  validation** and remote-save reconciliation. That client-trusted balance is exactly the
  soft spot repackaged builds exploit. **This spec keeps the balance server-authoritative
  (§0, §3) — stricter than the original on purpose.** Keep it that way.
- **Saves:** they sync a full encrypted blob with a server-side "save allowed" gate. For
  v1 you don't need blob saves at all — your state already lives in Postgres per §3.

## 7. Build order

1. `schema.sql` + seed one character (`persona.sample.json`).
2. `GET /characters`, `GET /characters/:id/story`, `GET /wallet`.
3. `POST /chat/:id/message` with the transaction + idempotency + LLM call.
4. `POST /wallet/daily-bonus`.
5. Wire the two client screens (`client/`) to 1–4.

---

## Appendix A — Auth & token flow (concrete)

Modeled on the analysed app's device-ID anonymous auth, adapted to our stack. Our
improvement: tokens live in the client's **secure storage** (expo-secure-store /
Keychain / Keystore), never plaintext prefs.

### Flow
```
first launch:  POST /v1/auth            { device_id }           -> { access, refresh }
every call:    Authorization: Bearer <access.token>
on 401:        POST /v1/auth/refresh    { refresh }             -> { access, refresh }
               (refresh fails -> re-run POST /v1/auth with device_id)
optional:      POST /v1/auth/heartbeat                          -> 204   (session liveness)
```
`device_id` is a client-generated stable UUID kept in secure storage. No password in v1.
Later, link an email/OAuth identity to the same user row without changing this flow.

### Token shape (both access and refresh)
```json
{ "token": "<opaque-or-jwt>", "expires_at": "2026-10-30T12:00:00Z" }
```
`POST /v1/auth` and `POST /v1/auth/refresh` both return:
```json
{ "access": { "token": "…", "expires_at": "…" },
  "refresh": { "token": "…", "expires_at": "…" } }
```
Server resolves `device_id` → a `users` row (create-on-first-seen), mints the pair, and
stores a hash of the refresh token. The client refreshes proactively before `expires_at`,
and reactively on a 401. Keep access-token lifetime short (e.g. 1h), refresh long (e.g. 30d).

> Client contract note: `client/lib/api.ts` currently takes an injected bearer via
> `setAuthToken()`. To adopt this flow, add an `auth.ts` that owns device_id + the token
> pair in secure storage, refreshes on 401, and calls `setAuthToken(access.token)`.

## Appendix B — AI request shape (server-side, concrete)

The **client never calls the model.** `POST /v1/chat/:id/message` (§2) sends just
`{ text, client_msg_id }`. The **server** then composes the model request. This is the
shape the server builds — modeled on the analysed app's `AIData`, with the persona held
server-side (the client never sends persona facts, so they can't be tampered with):

```json
{
  "user_profile": { "id": "<user_id>", "name": "<display_name>" },
  "bot_profile":  { "name": "Aria", "persona_facts": "<persona.system + beat.guidance>" },
  "context": [
    { "turn": "bot",  "message": "Red again. It kept lying to me." },
    { "turn": "user", "message": "why red?" }
  ],
  "chat_settings": { "allow_mature": false }
}
```
Mapping to our model call (see `backend/chat_message_reference.py`):
- `bot_profile.persona_facts` ← `persona.system` + `voice` + `safety` + current
  `beat.guidance` (all server-owned, from the `characters` row).
- `context` ← the last `HISTORY_TURNS` rows from `messages`, oldest-first, `turn` ∈
  `{user,bot}` (their keys were literally `"user"`/`"bot"`).
- `chat_settings.allow_mature` ← a **server-side** per-user flag (age-gate), never trusted
  from the client. (Theirs was `allow_nsfw` sent from the client — don't copy that; decide
  it on the server.)
- The new user `text` is appended as the final `{turn:"user"}` entry.

Reactions (optional, later): a `POST /v1/chat/:id/react { message_id, reaction }` maps to
their `AIReactionData` (thumbs up/down). Skip for v1.

### Free-message gating (their paywall hook, as an option)
Their `IAiMessagesProvider.ShouldActivatePaywall()` counts user messages and flips a
paywall. In our model the gem spend in §2 already does this job (out of gems → prompt to
claim the daily bonus or buy more). You don't need a separate message counter for v1.
