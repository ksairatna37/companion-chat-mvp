# MeChat — client architecture map (system level)

What the shipped client is made of, grouped by domain. This is **learn-from**
material: how a successful app in this genre decomposes its features. It reuses none
of their assets, content, prompts, or backend. Method-level detail (classes/methods/
fields) is being recovered separately into `il2cpp_types.md`.

**Evidence basis:** `assets/bin/Data/ScriptingAssemblies.json` lists 147 first-party
`PlayMe.MeChat.*` assemblies + the `PlayMe.Framework.*` / `BetterMe.Framework.*`
shared layer. Grouping and counts below are PROVEN (assembly names). Each
description of *what a system does* is INFERRED from its name + the string/vocabulary
evidence from earlier passes.

## The shape in one line

A Unity client that is **thin on "engine," thick on live-ops**: the core loop
(characters → chat → stories → gems) is a small fraction of the code; the large
majority is monetization, offers, events, and progression systems layered around it.
That ratio is the real lesson.

## Domains

### 1. Core loop (small — this is what your MVP needs)
- **Characters** (14 assemblies): `Characters`, `.General`, `.Matches`, `.MatchUpdate`,
  `.Npc`, `.Tags`, `.Exclusives`, `.AvatarSwitching`, `.AI`, `.Vip`(+`.Api`),
  `.Notifications`, `.AdsMatch`(+`.Provider`). The companion roster + how a user gets
  matched to one.
- **Chat** (1): the messaging surface.
- **Stories** / **StoriesUnlocking** / **PrepaidStories** / **CharacterStoryNavigation** /
  **VisualNovel**: the scripted interactive-fiction layer the chat sits inside.
- **AI** + **Characters.AI** + **AiBotsRemoval**: the AI-companion path. `AiBotsRemoval`
  is telling — AI chat is a **gated** feature a user can pay to remove limits on.
- **Feed** (5) + **Discovery** + **Recommendations** + **SearchEngine** +
  **CharactersSearch**(+`.Filters`) + **MatchProposal** (3: `.AutoMatch`,
  `.DateSideCharacters`): the swipe/match/browse front end that feeds users into chats.
- **Favorites**, **Profile**, **OnBoarding**, **Flow**, **App**.

### 2. Economy & monetization (large — deliberately deferred in your MVP)
- **Currencies**, **Rewarder**, **RewardInfoProvider**, **Rewards**(2), **DeferredRewarding**,
  **Shop**(2) + **ShopBonus**, **Store**(+`.Segmentation`), **Subscription**,
  **MerchShop**, **Promocodes**, **Referral**.
- **Offers** as a whole product category — one assembly *each*: `StarterOffer`,
  `OneTimeOffers`, `SmartOffer`, `FinishingOffer`, `PreDateOffer`, `LockedDateOffer`,
  `CharacterOffer`, `CharacterWithGemsOffer`(2), `CharacterWithRewardOffer`,
  `NoAdsWithGemsOffer`, `InstalmentOffer`, `OutfitOffer`, `PremiumOffers`. **13+ offer
  types** is the clearest signal of where their engineering goes.
- **Advertising**, **AdsReward**, **Characters.AdsMatch**: rewarded-ad economy wired
  into matching.

### 3. Progression & retention (medium — add after the loop works)
- **DailyBonus**, **DailyMissions**, **WeeklyMissions**, **Challenges**, **Quests**
  (via framework), **Achievements**, **PlayerTropes** / **Tropes**,
  **CharacterLevelRework**, **PiggyBank** (`Meta`), **RateUs**, **Tips**.
- **Event** (10): a full live-events subsystem — `EventStore`, `Features.Currency`,
  `Features.DailyBonus`, `Features.ExclusivePromotion`, `Features.GetLockedDate`,
  plus seasonal `SpookySeason`, `SpringFestival`, `LoveLetters`, `VisualNovel`. This
  is a reusable event *engine*, not one-off events.

### 4. Content pipeline (the Addressables story)
- **Content** (6): `.Stories`, `.Avatars`(+`.Configs`), `.AdditionalAvatars`,
  `.Npc`, `.Hub`. **ContentUpdate**, **ContentExposure**(+`.Api`), **AssetProvider**,
  **Configs**. Confirms the earlier finding: characters/stories/art ship as remote
  Addressables content, updated without an app release.

### 5. Cross-cutting services
- **Analytics**, **MarketingConversions**, **DeepLinks**, **Notifications** /
  **InGameNotifications** / **QuestNotifications**, **Localization**, **Audio**,
  **Video**, **Vibration**, **DarkTheme**, **UI**, **Saves**, **Secrets**,
  **AdultContent**(2 — maturity gating), **Spectrm**, **Social**/**SocialPages**.

### 6. Shared framework (`PlayMe.Framework.*`, `BetterMe.Framework.*`)
The reusable platform beneath MeChat (also powers the studio's other titles): `Http`
+ `Http.Auth`, `Id`, `Encryption`, `Saves`(+`.Remote`), `Store`(+`.Validation`,
`.Segmentation`), `Subscription`(native/web), `Analytics` (Amplitude/AppsFlyer/
Facebook/Firebase/TikTok platform adapters), `CloudFunctions`, `RemoteLoading`,
`RemotePrefab`, `ForceUpdate`, `Currencies`, `Wallet`, `Launcher`.

## What this tells you to build (and not build) for v1

| Their system | Your MVP | Why |
|---|---|---|
| Characters + Chat + Stories + AI | **Build** | This *is* the product. Keep it small. |
| Currencies/Wallet | **Build (minimal)** | One gem balance + daily bonus. Server-authoritative. |
| Feed/Discovery/Match | **Build (lite)** | A simple character list is enough at first. |
| 13+ Offer types, Subscription, MerchShop, Referral, Promocodes | **Skip** | This is the post-traction layer, not the proof. |
| Event engine (10), Missions, Achievements, PiggyBank | **Skip** | Retention scaffolding; premature until the loop is fun. |
| Content as remote Addressables | **Adopt the idea** | Serve characters/stories as JSON rows (already in `BACKEND_CONTRACT.md`). |
| Framework Http/Auth/Saves.Remote/Store.Validation | **Mirror the principle** | Server owns auth, saves, and economy validation — never the client. |

The headline: their *core experience* is maybe 15–20% of the codebase. A focused
original build can reproduce the feeling with the small core and grow the live-ops
later — which is exactly how `BACKEND_CONTRACT.md` and the client stubs are scoped.
