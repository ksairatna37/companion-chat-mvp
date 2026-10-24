# MeChat — server-side infrastructure map

Everything that runs on **their** servers (vs. the client code we recovered). This is
the reference so we don't re-derive it later. Each system notes its **evidence basis**
(PROVEN from the teardown / INFERRED from SDKs + endpoint shapes / GENERAL to the genre)
and **your equivalent**.

> Source material: `il2cpp_types.md`, `client_architecture.md`, the APK manifest + SDK
> inventory. We have the client's view of these systems (the request shapes, the SDKs
> wired in); we do **not** have their hosts, keys, prompts, prices, or server code — and
> we're not pursuing those (their IP/infra). This doc is for building an original.

## The 10 systems

### 1. Main API backend
- **Does:** serves the app's core REST surface — `/auth`, `/refresh`, `/heartbeat`, `/me`,
  `/me/custom_update`, `/me/permissions`, `/device`, `/save`, `/save-status`, `/validate/`,
  `/redeem`, `/referral/code`, `/referral/rewards`, `/getFilteredNpc`.
- **Evidence:** PROVEN (endpoint-path string literals + `HttpRequest` subclasses). Host is a
  **config value** `IHostsConfig.Release`, not in the APK.
- **Shape:** stateless app servers behind a load balancer + TLS. Uniform `HttpRequest<T>`
  JSON contract (UnityWebRequest + Newtonsoft on the client side).
- **Your equivalent:** `backend/app.py` → Flask/FastAPI on your own host. Core for v1.

### 2. LLM inference service  ⭐ most valuable, most hidden
- **Does:** generates the companion replies. Takes the `AIData` payload (persona + context +
  settings) and returns text. Holds the **prompts, model, and model keys**.
- **Evidence:** PROVEN it exists and is **separate** — `IAiStoryConfig.HostPath` /
  `HostPathReactions` are distinct from the main API host. Model/provider/prompt are HIDDEN
  (config + server-side).
- **Your equivalent:** the `call_llm()` seam in `app.py`. Pick a provider (deferred). Core for v1.

### 3. Databases
- **Does:** accounts, profiles, wallet + ledger, purchase/subscription state, relationship +
  story progress, encrypted remote-save blobs, analytics event stores.
- **Evidence:** INFERRED from the save/wallet/story/validation types + the `/save`, `/me`,
  `/validate` endpoints.
- **Your equivalent:** Postgres (`backend/schema.sql`). Core for v1.

### 4. Content delivery (CDN + content-ops pipeline)
- **Does:** serves Unity **Addressables** bundles — characters, stories, art — with a content
  catalog, so new content ships **without an app update**.
- **Evidence:** PROVEN — `cdn.playme.pro/mechat_test_bundles/`, the `Content*`/`ContentUpdate`/
  `AssetProvider` systems, and Addressables catalog strings.
- **Your equivalent:** serve characters/stories as **JSON rows** from the API for v1; add a real
  CDN + asset pipeline only when you have lots of media. Grow-later.

### 5. Payments / IAP validation
- **Does:** server-side Apple/Google **receipt validation**, entitlement granting, subscription
  lifecycle, LTV-based price segmentation.
- **Evidence:** PROVEN — `ServerPurchaseValidator`, `ValidationRequest` → `/validate/`
  (Receipt + InAppId + Platform + IsSubscription, 3 retries), `LtvSegmentProvider`.
- **Your equivalent:** skip for v1 (gems via daily bonus only). Add real IAP when monetizing.

### 6. Integrity / anti-abuse
- **Does:** device attestation + cheater detection + save reconciliation that backstops the
  **client-side wallet**.
- **Evidence:** PROVEN host `integrity.playme.studio`; `FakeSpentDetected`, `CheaterExposing`,
  `/save-status` gate.
- **Your equivalent:** mostly unnecessary — your wallet is **server-authoritative**, which
  removes the attack surface their client-trusted wallet has. Add rate-limiting only.

### 7. CloudFunctions (serverless)
- **Does:** a separate dev/prod serverless host for some operations outside the main API.
- **Evidence:** PROVEN — `BetterMe.Framework.CloudFunctions`, `CloudFunctionsSettingsSection
  { DevHost, ProdHost }`, a dedicated HTTP client.
- **Your equivalent:** not needed; fold into your API. (Or Vercel/Cloudflare functions later.)

### 8. Remote config service
- **Does:** serves feature flags, the AI host/token, prices, free-message limits, the prod host
  itself — at runtime, not baked into the app.
- **Evidence:** PROVEN by absence — so much came back as "config, not literal" (`IHostsConfig`,
  `IAiStoryConfig`, flag vocabulary like `ai_bot`, `ai_dialog_block`). Firebase Remote Config
  SDK is present.
- **Your equivalent:** env vars for v1; add a flags service (Firebase Remote Config / your own)
  when you run experiments. Grow-later.

### 9. Identity / auth service
- **Does:** device-ID → access/refresh token minting, refresh, heartbeat/session liveness.
- **Evidence:** PROVEN — `AuthRequest(deviceId)`, `TokensPairResponse`, `/auth` `/refresh`
  `/heartbeat`, `AuthorizedHttpClient`.
- **Your equivalent:** `/v1/auth*` in `app.py` (built + tested). Could be part of the API or
  split out later. Core for v1 (done).

### 10. Third-party managed services (their accounts/dashboards)
- **Does:** push, analytics, attribution, crash reporting, ads.
- **Evidence:** PROVEN from the APK's SDKs + manifest components:
  - **Firebase** — FCM push, Crashlytics, Analytics, Remote Config.
  - **OneSignal** — push notifications.
  - **AppsFlyer** — attribution + deep links (`mechat.onelink.me`).
  - **Amplitude** — product analytics (`api2.amplitude.com`).
  - **Ad mediation** — AppLovin MAX + ironSource, AdMob, Meta Audience Network, Vungle,
    Chartboost, Fyber, Mintegral, Pangle, Unity Ads (via SafeDK).
  - **Facebook** SDK, **TikTok** Business SDK.
- **Your equivalent:** your OWN projects, added as needed — push + one analytics tool early;
  attribution + ads much later. Never inherit theirs.

## Summary: what's core vs. later for YOUR build

| # | System | v1? |
|---|--------|-----|
| 1 | API backend | **Core** (`app.py`) |
| 2 | LLM service | **Core** (`call_llm()` seam) |
| 3 | Database | **Core** (Postgres) |
| 9 | Auth/identity | **Core** (done, part of API) |
| 4 | Content CDN/pipeline | Later (JSON rows for now) |
| 8 | Remote config | Later (env vars for now) |
| 10 | Push + analytics | Early-ish (your own projects) |
| 5 | IAP validation | When monetizing |
| 6 | Integrity/anti-abuse | Mostly N/A (server-authoritative wallet) |
| 7 | CloudFunctions | Fold into API |

**~3 core systems to start** (API + DB + LLM; auth is part of the API). The deployment plan
for those is in `spec/DEPLOYMENT.md`.
