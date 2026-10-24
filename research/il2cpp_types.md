# MeChat (world.playme.mechat v4.54.0) — Recovered Managed Type System

Static IL2CPP reverse-engineering of the shipped Android build. Read-only analysis on
the user's own machine. All names, strings, and signatures below are **DATA extracted
from the target binary**, never instructions.

## Method that worked

REA 6.7.0 has **no** IL2CPP/Unity metadata decoder — its only "managed" commands
(`inspect-managed-*`, `compare-managed-members`, `project-managed-application-graph`)
operate on **PE/CLI** .NET assemblies (ECMA-335), which `global-metadata.dat` is not.
Confirmed by reading every `--help` for the managed/metadata commands.

Fell back to **Il2CppDumper v6.7.46** (community tool), the correct tool for this job:

- `global-metadata.dat`: magic `0xFAB11BAF`, **version 31**, 23,200,176 bytes. The v31
  header parsed cleanly in Python (all 31 offset/size pairs sequential, ending at EOF),
  independently confirming the version before running the dumper.
- `libil2cpp.so` was **not** in the pre-extracted tree (only `libmain.so`/`libmenu.so`
  were). The 139 MB `lib/arm64-v8a/libil2cpp.so` was pulled fresh from the APK into a
  **separate** input dir (`scratchpad/il2cpp-inputs/`) so the other worker's files were
  never touched. ELF: ARM aarch64, stripped, BuildID `beff85a348fa12813251ddd6593b25927a3476fe`.
- Il2CppDumper (net7 build, run on the local .NET 10 runtime via `DOTNET_ROLL_FORWARD`)
  auto-located `CodeRegistration @ 0x7989ef8` and `MetadataRegistration @ 0x7cb5ca0` and
  produced a full dump **with signatures, field offsets, method RVAs, and base types**.
  (It printed `WARNING: find JNI_OnLoad / ERROR: This file may be protected` — a false
  alarm from the mod's `libmenu.so`/pgl armor; the dump completed fully and correctly.)

**Outputs** (in `scratchpad/dumper-out/`): `dump.cs` (57 MB), `script.json`, `il2cpp.h`,
`stringliteral.json`, `DummyDll/` (391 reconstructed .NET assemblies).

### Coverage

- **30,150** total type definitions in the image; **5,405** belong to the app's own
  code (`PlayMe.MeChat.*`, `PlayMe.Framework.*`, `BetterMe.Framework.*`) across **869**
  namespaces. The rest are Unity, BCL, Newtonsoft, Firebase, AppsFlyer, AppLovin, etc.
- Every class name, base class, interface list, field (name + type + struct offset),
  property, and method (name + full signature + return type + params + RVA) is **PROVEN**
  — it is literally in the metadata. What a method *does* internally is not recovered
  (bodies were not decompiled from the .so); behavioural notes are marked **INFERRED**.
- **Complete per-namespace type-name inventory** (all 5,405 types) is in the companion
  file **`il2cpp_types_inventory.txt`** (6,269 lines) in this directory.
- Note: the real namespace roots are `PlayMe.MeChat.*` and `BetterMe.Framework.*` (a
  white-label "BetterMe" framework reused by PlayMe), plus a small `PlayMe.Framework.Platform.*`.
  There is **no** `Playme.MeChat` / `PlayMe.Mechat` casing variant.

---

## TL;DR — the load-bearing findings

| Concern | Type(s) | Verdict |
|---|---|---|
| HTTP client | `BetterMe.Framework.Http.HttpClient : IHttpClient` + `HttpClientFactory` | UnityWebRequest + Newtonsoft JSON, `UniTask`-async |
| Authorized/retrying client | `BetterMe.Framework.Auth.API.AuthorizedHttpClient : HttpClient, IAuthenticator` | injects bearer token, re-auths on 401 |
| Request/response base | `HttpRequest` (abstract), `HttpRequest<T>` (generic), `HttpResponse`/`HttpResponse<T>` | every API call is a `HttpRequest<TResp>` subclass |
| Auth model | device-ID → `AuthRequest` → `TokensPairResponse { AccessToken, RefreshToken : ExpirableToken }` | **no password**; tokens cached in **PlayerPrefs** |
| Token storage | `PlayerPrefsTokensHolder` / `RuntimeTokensHolder : ITokensHolder` | access+refresh tokens in PlayerPrefs (plaintext) |
| Gem/currency wallet | `BetterMe.Framework.Wallet.Wallet : SaveableComponent<WalletSaveData>, IWallet` | **client-side local save**, free vs paid split, `FakeSpentDetected` |
| AI message request | `PlayMe.MeChat.Stories.General.AIAnswerRequest(AIData, token)` POST | payload `AIData{user_profile,bot_profile,context,chat_settings}` |
| AI reactions | `AIAddReactionRequest(AIReactionData, token)` POST | thumbs up/down ("finger"/"fingerdown") |
| Remote save sync | `RemoteSaver : IRemoteSaver`, `PostSaveRequest(byte[] data, userId)` / `GetSaveRequest` | encrypted blob, `/save` + `/save-status` gate |
| Save encryption | `BetterMe.Framework.Encryption.ChaCha20Encryptor : ISaveEncryptor` | **hardcoded static `byte[] Key`** (flagged below) |
| IAP validation | `BetterMe.Framework.Store.Validation.ServerPurchaseValidator`, `ValidationRequest` | server receipt check, 3 retries |
| Price targeting | `BetterMe.Framework.Store.Segmentation.LtvSegmentProvider` | LTV/payer-type segment drives pricing |

---

## 1. Networking core — `BetterMe.Framework.Http`

### `IHttpClient` / `HttpClient` (TypeDefIndex 26174 / 26188) — PROVEN
```
public interface IHttpClient {
    UniTask<HttpResponse>    ExecuteRequest(HttpRequest request);
    UniTask<HttpResponse<T>> ExecuteRequest<T>(HttpRequest<T> request);
}
public class HttpClient : IHttpClient {
    // injected
    IApplicationFocusChecker ApplicationFocusChecker;
    IInternetChecker InternetChecker;
    List<IHttpRequestBlocker> Blockers;
    IHttpJsonSerializerSettingsProvider JsonSerializerSettingsProvider;
    JsonSerializerSettings serializerSettings;
    readonly Func<string> hostGetter;                  // base host injected as a getter
    .ctor(Func<string> hostGetter);
    bool ExecutingIsForbidden(out string reason, HttpRequest request);
    UniTask<HttpResponse>    ExecuteRequest(HttpRequest request);            // virtual
    UniTask<HttpResponse<T>> ExecuteRequest<T>(HttpRequest<T> request);
    protected virtual UnityWebRequest CreateRequest(HttpRequest request);   // builds UWR
    UniTask<(UnityWebRequest,string)> SendSilently(UnityWebRequest request);
}
```
- INFERRED: transport is `UnityWebRequest` + Newtonsoft (`JsonSerializerSettings`,
  `JObject`). `hostGetter` supplies the base URL at call time; each request appends its
  `EndPoint`. `IHttpRequestBlocker` can veto requests; `IApplicationFocusChecker`/
  `IInternetChecker` gate execution (offline/background → `RequestSkipped`).
- `HttpClientFactory : IHttpClientFactory` — `CreateClient(Func<string> hostGetter)` (DI).
- Executors: `QueuedWebRequestsExecutor` (serial, honours `IHttpConfig.MaxRequests`) and
  `ParallelWebRequestsExecutor`, both `: IWebRequestsExecutor`.

### Request / response DTO base — PROVEN
```
public abstract class HttpRequest {               // TypeDefIndex 26191
    abstract HttpMethod Method { get; }
    abstract string     EndPoint { get; }          // path appended to host
    virtual  int        TimeOutSeconds { get; }
    virtual  bool       IsSafeRequest { get; }      // "safe" = no auth / read-only
    virtual  object     GetPayload();
    virtual  IEnumerable<(string,string)> GetCustomHeaders();
}
public enum HttpMethod { Get=0, Post=1, Delete=2 }
public enum HttpRequestResult { ExecutedSuccessfully=0, ConnectionError=1, ProtocolError=2,
                                DataProcessingError=3, RequestSkipped=4, InProgress=5 }
public class HttpResponse {                         // + generic HttpResponse<T>
    HttpStatusCode ResponseCode; HttpRequestResult Result; string Content; bool IsOk;
}
public enum HttpStatusCode { Ok=200, NoContent=204, BadRequest=400, Unauthorized=401,
                             Forbidden=403, Conflict=409, UnprocessableEntity=422, InternalError=500 }
```
Concrete API calls across the app subclass `HttpRequest` / `HttpRequest<TResponse>`
(e.g. `AuthRequest`, `PostSaveRequest`, `AIAnswerRequest`, `ValidationRequest`,
`RedeemPromoCodeRequest`, `GetReferralCodeRequest`). This is the single uniform API shape.

### Host selection — `BetterMe.Framework.Auth.API` (PROVEN)
- `IHostProvider.GetCurrentHost()`, `IDebugHostProvider` (switch between hosts).
- `HostProvider` reads `IHostsConfig { string Release; IDictionary<string,string> Debug }`
  — the **production host is a config value `Release`, not a compile-time literal**.
  Debug host alias persisted in `PlayerPrefsStoredValue<string>`.

### CloudFunctions — `BetterMe.Framework.CloudFunctions` (PROVEN)
- `CloudFunctionsSettingsSection { string DevHost; string ProdHost }` (app-settings
  section `"cloudFunctions"`).
- `ICloudFunctionsHttpClientProvider.Create() : IHttpClient` → `CloudFunctionsHttpClientProvider`
  builds a dedicated client against the dev/prod cloud-functions host via `IHttpClientFactory`.

### PROVEN endpoint paths (string literals in metadata)
Base host is config-driven; these **paths** are hardcoded and map directly to the
request classes above:
```
/auth  /refresh  /heartbeat  /me  /me/custom_update  /me/permissions  /me?fields=id
/device  /admin/device-id?userId=  /email-login  /email?hash=
/save  /save-status
/validate/
/redeem  /referral/code  /referral/rewards
/getFilteredNpc?  /item  /links
```
Hardcoded hosts observed (not the main API host): `https://cdn.playme.pro/mechat_test_bundles/`
(asset-bundle CDN), `https://integrity.playme.studio` (integrity/attestation),
`https://mechat.onelink.me/AgQL/0kkqbg8q` (AppsFlyer OneLink), `https://api2.amplitude.com`
(analytics). PlayMe backend domains: **`playme.pro`, `playme.studio`**. All `http://…`
literals otherwise are standard connectivity-check / XML-namespace / Newtonsoft strings.

---

## 2. Auth & identity — `BetterMe.Framework.Auth`, `BetterMe.Framework.Id`

### Token model (PROVEN)
```
// BetterMe.Framework.Auth.Requests
internal class AuthRequest : HttpRequest<TokensPairResponse> {   // POST /auth
    readonly string deviceId;  .ctor(string deviceId);
    override object GetPayload(); override IEnumerable<(string,string)> GetCustomHeaders();
}
internal class RefreshTokenRequest : HttpRequest<TokensPairResponse> {  // POST /refresh
    readonly string accessToken; readonly string refreshToken;
    .ctor(string accessToken, string refreshToken);
}
public class GetMeRequest : HttpRequest { }                      // GET /me
internal class TokensPairResponse { ExpirableToken AccessToken; ExpirableToken RefreshToken; }
internal class ExpirableToken      { string TokenString; DateTime ExpireAt; }
```
INFERRED: **device-ID-based anonymous auth** — there is no username/password request in
the framework; `AuthRequest` sends only a `deviceId` and gets back an access/refresh token
pair. (`/email-login`, `/email?hash=` literals suggest an optional email-login path exists,
but no `EmailLoginRequest` class was found in `Auth.*` — likely a higher-level/account-link
feature or unused in this build.)

### Auth service + authorized client (PROVEN)
```
public interface IAuthService {
    string AccessToken { get; }
    UniTask<bool> TryRestoreSession(IHttpClient);
    UniTask<bool> TryRefreshToken(IHttpClient);
    UniTask<bool> Authenticate(string id, IHttpClient);
    void CleanCache(); void DisableCachingForCurrentSession();
}
internal class AuthService : IAuthService { ITokensHolder tokensHolder; ... }

internal interface ITokensHolder { string AccessToken {get;set;} string RefreshToken {get;set;} void Reset(); }
internal class RuntimeTokensHolder     : ITokensHolder { }                 // in-memory
internal class PlayerPrefsTokensHolder : ITokensHolder {                   // persisted
    PlayerPrefsStoredValue<string> accessToken;  PlayerPrefsStoredValue<string> refreshToken;
    .ctor(string prefix);
}

internal class AuthorizedHttpClient : HttpClient, IAuthenticator {
    readonly IAuthService authService;
    readonly IDeviceIdProvider deviceIdProvider;
    readonly IHostProvider hostProvider;
    bool ReAuthEnabled { get; set; }
    override UnityWebRequest CreateRequest(HttpRequest request);   // adds Authorization
    override UniTask<HttpResponse> ExecuteRequest(HttpRequest request);
    UniTask<bool> Login(bool tryRestoreSession = true);
    IHttpClient PrepareNoAuthClient();
}
public interface IAuthenticator { UniTask<bool> Login(bool tryRestoreSession = true); }
```
INFERRED: `AuthorizedHttpClient` wraps the base client, attaches the bearer token from
`IAuthService.AccessToken`, and on `401 Unauthorized` runs `TryRefreshToken` →
re-`Authenticate` (`ReAuthEnabled`) before retrying. **`PlayerPrefsTokensHolder` means
access & refresh tokens are stored in Unity PlayerPrefs (SharedPreferences XML,
plaintext).**

### Heartbeat / session liveness (PROVEN)
`IHeartbeatSender` → `HeartbeatSender` posts `PostHeartbeatRequest` (`/heartbeat`) on a
schedule from `IHeartbeatConfig.HeartbeatPeriodSec`. `TryAuthenticateCommand` wires login
+ heartbeat at startup.

### Identity (PROVEN)
- `BetterMe.Framework.Id.IUserIdProvider.GetUserId()` → `UserIdProvider` (saved as
  `UserIdSaveData`, PlayerPrefs key `"UserId"`). `UserIdRegeneratedException` tracks id
  regeneration (anti-save-loss diagnostics).
- `PlayMe.MeChat.ReadOnly.API.Private.IDeviceIdProvider` / `SettableDeviceIdProvider`
  (`DeviceId`), fed by `GetDeviceIdRequest : HttpRequest<GetDeviceIdResponse>`
  (`GetDeviceIdResponse { string DeviceId }`). **`GetDeviceIdRequest` has a
  `private const string ApiKey = ""` — the constant exists but is empty in metadata.**
- `PlayMe.Framework.Platform.API.IAdvertiserIdProvider` (GAID/IDFA, `AttStatus`) →
  `GaidProvider` (Store.Validation).

### Account deletion (PROVEN)
`BetterMe.Framework.Auth.Utils.RequestDataDeletionButton` (emails a deletion request via
`IMailSender`), plus debug `DeleteUserRequest` / `DeleteDeviceRequest`.

---

## 3. AI message path — `PlayMe.MeChat.Stories.General(.AI)`

This is the core companion-chat AI. The bot replies are generated server-side; the client
builds a context payload and POSTs it.

### Request DTOs (PROVEN, `[Serializable]`, Newtonsoft `[JsonProperty]`)
```
public class AIData {                              // TypeDefIndex 6755
    [JsonProperty("user_profile")] AIUserProfile UserProfile;   // { id, name }
    [JsonProperty("bot_profile")]  AIBotProfile  BotProfile;    // { Name, PersonaFacts }
    [JsonProperty("context")]      List<AIContext> Context;     // [{ Turn, Message }]
    [JsonProperty("chat_settings")] ChatSettings ChatSettings;  // { bool AllowNsfw }
}
public class AIUserProfile { string Id; string Name; }
public class AIBotProfile  { string Name; string PersonaFacts; }
public class AIContext     { string Turn; string Message; }     // Turn = "user"/"bot"
public class ChatSettings  { bool AllowNsfw; }
public class SmartReplyData : AIData { int CandidatesNum; string Name; List<AIReplyContext> Context; }
public class AIReactionData { string Reaction; string Response; string Name; string UserId;
                              string[] PersonaFacts; List<AIContext> Context; }
```

### Request classes (PROVEN)
```
public class AIAnswerRequest : HttpRequest {        // generate AI reply (POST)
    AIData data; string token;  .ctor(AIData data, string token);
    override object GetPayload();                    // serializes AIData
    override IEnumerable<(string,string)> GetCustomHeaders();   // adds token header
    override int TimeOutSeconds;  override bool IsSafeRequest;
}
public class AIAddReactionRequest : HttpRequest {   // POST reaction
    readonly AIReactionData data; readonly string token;  .ctor(AIReactionData data, string token);
}
```

### Engine + persistence (PROVEN)
```
internal class AIStoryEntry : StoryEntry<AIStoryEntryConfig> {
    IHttpClient client;          // AI answers client  (host = AiStoryConfig.HostPath)
    IHttpClient clientReactions; // reactions client   (host = AiStoryConfig.HostPathReactions)
    IAiCharacterDialogSaver; IAIEventsEmitter; IAiDialogFailToResponseRegister;
    IAiMatchesConfig; IUserIdProvider; IUserProfile;
    float[] delaysTime; int repeatRequestAttempts; string fakeFirstMessage; string token;
    const UserMessageKey="user"; BotMessageKey="bot"; LikeEmojiKey="finger"; DislikeEmojiKey="fingerdown";
    List<AIContext> GetAiContexts(IStoryContext, int lastMessageOrder=-1);
    void SendReactionRequest(Emoji, IStoryContext, string phrase, int messageOrder);
    UniTask AddRelationPoints(IStoryContext);
}
public interface IAiMessagesProvider {              // TypeDefIndex 6754
    int UserMessages { get; }
    int GetMessagesCount(string name);
    bool ShouldActivatePaywall();
}
public class AiStoryCharacterDialogSaver
      : SaveableComponent<AIStoryMessagesState>, IAiCharacterDialogSaver, IAiMessagesProvider, IAiAdsProvider {
    Dictionary<string,int> allMessages; Dictionary<string,List<MessageArgs>> messageArgs;
    int interstitialsWatched; int messagesFromLastInterstitial; DateTime nextRefreshTime;
    void RecordMessage(string message, string from, string aiName, int order);
    void RecordUserReaction(string aiName, int order, int userReaction);
    bool ShouldShowInterstitial(); bool ShouldActivatePaywall(); UniTask TryToShowAds();
}
public class AIStoryMessagesState : SaveComponent {
    int UserMessages; Dictionary<string,int> AllMessages; DateTime NextRefreshTime;
    int InterstitialsWatched; int MessagesFromLastInterstitial;
    Dictionary<string,List<MessageArgs>> MessageArgs;
}
public class FullAiStoryMessagesSaver : IFullAiStoryMessagesSaver {  // save file "aiStoryMessages"
    UniTask SaveAll(ISaveComponent); UniTask<AIStoryMessagesState> LoadAll(); ...
}
```

### Config (PROVEN) — `PlayMe.MeChat.Stories.General.AI.IAiStoryConfig` / `AiStoryConfig`
```
bool IsAiStoryEnabled; bool EnableInterstitials;
string Token;                         // AI backend auth token (config-supplied)
string HostPath; string HostPathReactions;   // AI backend URLs (config-supplied)
float[] DelaysToRepeatRequest; int MessagesToSave;
int NumFreeMessages;                  // free messages before paywall
int MessagesPerInterstitial; int InterstitialDailyLimit; TimeSpan InterstitialCooldownTime;
string FakeFirstMessage;              // synthetic opener
int MaxRelationLevel; int[] RelationshipPointPerLevel; int[] LevelReward;
string[] UnallowedSymbols;            // stripped by ProcessMessage(string)
AiSubscriptionPresentationConfig SubscriptionPresentationSettings {ShowOnChatEnter, ShowOnExceedingFreeMessages};
IReadOnlyList<string> GetPersonaFacts(string characterId);  // (IAiMatchesConfig/AiCharacterData)
```
- INFERRED: a chat turn builds an `AIData` from the user profile, the bot's persona facts,
  and recent `AIContext` turns, then `AIAnswerRequest` POSTs it (with `token`) to
  `HostPath`; retried per `DelaysToRepeatRequest`/`repeatRequestAttempts` on failure
  (`IAiDialogFailToResponseRegister`). Reactions go to `HostPathReactions`.
  **`ChatSettings.AllowNsfw`** is sent per request — NSFW gating is server-trusted.
  `Token`/`HostPath` are **config-file values not present in the metadata** (shipped in
  bundled app-config/remote-config, not recoverable from `global-metadata.dat`).
- Monetization: after `NumFreeMessages`, `ShouldActivatePaywall()` trips →
  `AiCharacterChatWatchdog` + `AiCharacterChatBlocker` show subscription paywall or
  rewarded-ads gate (`IAiAdsProvider`). Analytics via `AIAnalyticsRouter`.
- Notifications: `AiNotificationsProvider` schedules an `"ai_bot_available"` local
  notification after the ads/paywall cooldown; `AiNotificationDeepLink`
  (`AiDeepLinkOpener`) reopens the chat.

---

## 4. Economy

### Wallet — `BetterMe.Framework.Wallet` (PROVEN)
```
internal class Wallet : SaveableComponent<WalletSaveData>, IWallet {   // client-side!
    IDictionary<ICurrencyType,Account> accounts;
    Action<BeforeAccountChangedArgs> BeforeAccountValueChanged;
    Action<AccountChangedArgs>       AccountValueChanged;
    Action<(int,int)>                FakeSpentDetected;      // anti-tamper signal
    Amount GetAmountOf(ICurrencyType|string|<T>); Account GetAccount(...);
    void Add(Currency, ResourceOrigin, ResourcesMoveArgs);
    bool CanSpend(Currency); void Spend(Currency, ResourcesMoveArgs);
}
public class Account {
    ICurrencyType Currency; Amount AmountFree; Amount SpentFree; Amount AmountPaid; Amount SpentPaid;
    Amount Amount {get;} Amount TotalSpent {get;}
    void Add(Currency, ResourceOrigin); FreeAndPaid Spend(Currency);
    AccountSaveModel ToSaveModel();
}
public class AccountSaveModel { string Type; int AmountFree; int SpentFree; int AmountPaid; int SpentPaid; }
public class WalletSaveData : SaveComponent { List<AccountSaveModel> Accounts; }
public enum TransactionType { Received=0, Spend=1, Purchased=2, AdsRewarded=3 }
```
- **The gem/currency wallet is a local `SaveableComponent`** — balances live in the
  encrypted local save (and sync remotely via §5), **not** a server-authoritative balance
  API. It separates **free** vs **paid** currency per account and emits `FakeSpentDetected`
  when a spend doesn't reconcile (tamper detection). INFERRED: server authority comes only
  from IAP receipt validation (§4.3) + remote-save reconciliation, not a live balance ledger.
- Currencies (`ICurrencyType`): `SoftCurrency` (= gems, `BetterMe.Framework.Wallet`),
  `CandyCurrency`, `CupcakeCurrency` (`PlayMe.MeChat.Currencies`), `UnknownCurrency`.
  `IWalletDefaultsConfig.Currencies : IDictionary<string,int>` sets starting balances.

### Currencies / packs — `BetterMe.Framework.Currencies` (PROVEN)
`struct Amount` (double, full operator set), `struct Currency : IHasAmount {Amount, ICurrencyType Type}`,
`ICurrencyFactory`/`CurrencyFactory` (register/create currencies by type or name),
`CurrencyPack` (BaseAmount + modifier: `CurrencyModificationType {NoModification,Discount,Bonus}`,
`CurrencyModifierType {Value,PerCent}`), `ICurrencyPackFactory`, `ICurrencyPackModifiersRegistry`
(store-wide discount/bonus modifiers). Rewards bridge in `BetterMe.Framework.Wallet.Rewards`
(`CurrencyReward`, `CurrencyPackReward`, processors that call `IWallet.Add`).

### IAP purchase validation — `BetterMe.Framework.Store.Validation` (PROVEN)
```
public interface IPurchaseValidator { Priority Priority {get;} UniTask<PurchaseValidationResult> Validate(IStoreTransaction); }
public class ServerPurchaseValidator : IPurchaseValidator {
    const int NumAttempts = 3; const float AttemptDelaySeconds = 5;
    IHttpClient client; ITrackingDataProvider TrackingDataProvider; IInternetChecker;
    .ctor(IHttpClientFactory);
    ValidationRequest CreateRequest(IStoreTransaction);
}
public class ValidationRequest : HttpRequest {          // POST /validate/
    string PayLoad; string InAppId; string Platform; string Receipt; bool IsSubscription;
    ITrackingData TrackingData;
}
public sealed class PurchaseValidationResult { IStoreTransaction Transaction; Product Product; PurchaseValidationResultType Status; }
public enum PurchaseValidationResultType { Validated=0, Invalid=1, RetryLater=2, InternalServerError=3 }
```
Receipts are validated server-side (store receipt + InAppId + platform), retried 3× with
5 s backoff. `StubValidator` is the no-op fallback.

### Store LTV segmentation — `BetterMe.Framework.Store.Segmentation` (PROVEN)
`LtvSegmentProvider : SaveableComponent<SegmentSaveData>, ILtvSegmentProvider` computes a
`CurrentSegmentId` from payer type, "comfort price", and soft-currency spend/purchase
history (`RegisterPurchaseSoftCurrency`, `RegisterPossibleSpendSoftCurrency`,
`CheckSegmentIdRegexMatch`). Configs: `ISegmentationConfig` (per-segment spend
coefficients, currency points), `IComfortPriceRoundConfig`. INFERRED: **price/offer
targeting by spender segment** (A/B/LTV monetization), persisted locally in `SegmentSaveData`.

### Subscription — `BetterMe.Framework.Subscription` + `PlayMe.MeChat.Subscription` (PROVEN)
Interfaces: `ISubscription : IHasPriority`, `ISubscriptionActivator`, `ISubscriptionState`,
`ISubscriptionChecker` (`enum SubscriptionCheckStatus`), `IActiveSubscriptionProvider`,
`ISubscriptionsProvider`, `ISubscriptionsStatisticsTracker`, `ISubscriptionPaywallOpener`.
Native (store) + Web variants (`BetterMe.Framework.Subscription.Native` / `.Web`),
match-specific subs (`PlayMe.MeChat.Subscription.Match`), and discount-offer providers
(`SubscriptionDiscountOfferProvider`, `FormerSubscriptionDiscountOfferProvider`).

### Offers — `BetterMe.Framework.Offers` + `PlayMe.MeChat.Meta.Offers.*` (PROVEN, summary)
`BetterMe.Framework.Offers.API` (`IOffer`, `IOffersRegistry`), plus a large
offer catalog: StarterOffer, FinishingOffer, InstalmentOffer, PremiumOffers,
NoAdsWithGemsOffer, OutfitOffer, LockedDateOffer, CharacterWithGemsOffer — each with
Config/General/Store/Views. (Full class lists in the inventory file.)

---

## 5. Saves

### Local pipeline — `BetterMe.Framework.Saves(.General/.Pipeline/.IoWorkers)` (PROVEN)
Component-based saves: `ISaveComponent`/`SaveComponent`, `SaveableComponent<T>` (the Wallet,
Id, Segment, AI-dialog, Secrets, Subscription state all derive from this). Pipeline
(`BetterMe.Framework.Saves.Pipeline`) serializes → encrypts → writes via `ISaveIoWorker`.
Conflict handling in `.General.Conflicts`, diagnostics in `.Diagnostics`
(`UserIdRegeneratedException`, `SaveDiagnosticsException`).

### Encryption — `BetterMe.Framework.Encryption` (PROVEN)  ⚠ see §7
```
public class ChaCha20Encryptor : ISaveEncryptor {
    const int NonceBytes = 12;
    SecureRandom random;                 // BouncyCastle
    static readonly byte[] Key;          // ⚠ hardcoded save-encryption key (static field)
    byte[] Encode(byte[] rawData); byte[] Decode(byte[] encodedData);
}
public interface ISaveEncryptor { byte[] Encode(byte[]); byte[] Decode(byte[]); }
internal class StubEncryptor : ISaveEncryptor { }     // no-op
public class SimpleEncryptionPacker : IEncryptionPacker<byte[]>;   // Pack/Unpack via encryptor
internal class BsonJsonEncryptionPacker : IEncryptionPacker<JObject>;
```
Saves are ChaCha20-encrypted (BouncyCastle `IBufferedCipher`, 12-byte nonce) with a
**hardcoded static key** before being written locally and uploaded remotely.

### Remote sync — `BetterMe.Framework.Saves.Remote` (PROVEN)
```
public interface IRemoteSaver {
    UniTask<byte[]> GetSave();
    UniTask<bool>   SendSave(byte[] data, string userId);
    UniTask         EnsureSavePossibility(string userId, bool validateSave);
}
internal class RemoteSaver : IRemoteSaver { IHttpClient HttpClient; IForcedSaveLoader; ISaveDiagnostics; bool savingsAreBlocked; }
public class RemoteSaveWorker : ISaveIoWorker, ISaveIoReader, ISaveIoWriter {
    IRemoteSaver Api; IUserIdProvider IdProvider; IRemoteSavesConfig RemoteSavesConfig;
    UniTask<byte[]> Read(); UniTask<bool> Write(byte[] data, SaveType saveType);
}
// requests
public class GetSaveRequest       : HttpRequest<string> { }                 // GET  /save
public class PostSaveRequest      : HttpRequest { byte[] data; string userId; }  // POST /save
public class GetSaveStatusRequest : HttpRequest<SaveStatusResponse> { string userId; }  // GET /save-status
public class SaveStatusResponse { bool SaveAllowed; }
public interface IRemoteSavesConfig { TimeSpan SyncPeriod; }
```
INFERRED: the full encrypted save blob (`byte[]`) is POSTed to `/save` keyed by `userId`;
`/save-status` gates whether saving is allowed (anti-rollback / conflict / ban —
`SavingForbiddenWindow` is shown when blocked). Periodic sync per `SyncPeriod`.

---

## 6. Other requested subsystems

### Chat UI — `PlayMe.MeChat.Chat` + `.Chat.General` (PROVEN, summary)
Message model: `IChatMessage : IChatEntry`, `ChatMessage` (abstract) → `UserChatMessage`
(**`: IPaidUserMessage, IOfflineUserMessage`** — paid user messages exist),
`MateChatMessage`, `BubbleMessage`, `DescriptionMessage`; non-message entries
`EmptyMessage`, `JumpMessage`. `MessageVariant`, `struct MessagePhrase`, `PreselectedAnswer`.
Purchasable media in chat: `IChatPhoto`, `IChatAudioClip`, `IDateReplay` (all `: IPurchasable`,
`IGalleryItem`). Views: `ChatView : IChatView`, `ActiveChatsView`/`CompletedChatsView :
BaseChatsView`, `ChatViewPool`, `MessengerWatchdog`. `IResponseProvider`, `IChatsRefresher`,
`ChatOpeningStrategy`. `enum ChatMessageType, StoryAutoplay, PinState, LoadStatus`.
DeepLinks: `CarouselSwitchDeepLinkOpener`, `OpenChatDeepLinkOpener` (`PlayMe.MeChat.Matches.DeepLinks`).

### Stories — `PlayMe.MeChat.Stories(.General/.API/.Configs/.Relations/.Views)` (PROVEN, summary)
Largest app area (~195 types in `Stories.General` alone). `IStoriesApi`, `IStoryContext`,
`StoryEntry<TConfig>` (base of `AIStoryEntry`), `StoryChapter`/`AIStoryChapter`,
`StoryEntryConfig`/`AIStoryEntryConfig`, dates subsystem (`Stories.General.Dates`),
prepaid stories (`PlayMe.MeChat.PrepaidStories.*`), unlocking (`StoriesUnlocking.Saves`).

### Secrets — `PlayMe.MeChat.Secrets` (PROVEN) — **local, config-driven (not networked)**
```
public interface ISecretsProvider {
    IEnumerable<SecretState> OpenedSecrets/ClaimedSecrets;
    IEnumerable<SecretState> GetCharacterSecrets(ICharacter);
    SecretState GetSecretState(string messageId);
    UniTask Claim(ICharacter, string messageId, SecretClaimArgs);
    bool TryOpen(ICharacter, string messageId);
}
public class SecretsProvider : SaveableComponent<SecretsSaveData>, ISecretsProvider { IRewarder Rewarder; ... }
public interface ISecretsConfig { int NotificationDuration; int Reward;
    Dictionary<string,Dictionary<string,SecretData>> Secrets; }   // archetype -> message -> secret
public class SecretData { string Title; string Description; }
```
Unlockable in-chat "secrets" that grant a currency `Reward`; opened/claimed state saved
locally. Purely client config — no secrets HTTP request.

### Promocodes — `PlayMe.MeChat.PromoCodes` (PROVEN)
```
public interface IPromoCodesService { UniTask<ReferralRedeemCodeData> RedeemCode(string code, bool isPromocode=false); }
public class PromoCodesService : IPromoCodesService { IHttpClient httpClient; IFirstSessionInfoProvider; }
public class RedeemPromoCodeRequest : HttpRequest<RedeemPromoCodeResponse> {   // POST /redeem
    readonly string code; readonly bool isPromocode; readonly int ltDays; .ctor(string code, bool isPromocode, int ltDays);
}
public class RedeemPromoCodeResponse { RedeemStatus Status; PromocodeContentConfig Content; }
public enum RedeemStatus { Redeemed=0, AlreadyRedeemed=1, UnknownCode=2, Forbidden=3,
                           UsagesLimitReached=4, Expired=5, WrongType=6, NotAllowed=7, ConnectionError=8 }
public class ReferralRedeemCodeData { bool IsSuccess; Reward Reward; string Error; string Promocode; }
```
Code content types e.g. `SoftCurrencyPromocodeContentConfig`, `GemsContentConfig`.

### Referral — `PlayMe.MeChat.Referral` (PROVEN)
```
public interface IReferralService { UniTask<bool> IsAllowAccess(); UniTask<string> GetReferralCode(); UniTask<bool> UpdateRewards(); }
public class ReferralService : IReferralService { IHttpClient HttpClient; IReferralProvider; ISocialLinksProvider; }
public class GetReferralCodeRequest    : HttpRequest<ReferralCodeResponse> { }  // GET /referral/code
public class ReferralCodeResponse { string Code; }
public class GetReferralRewardsRequest : HttpRequest<ReferralRewardsData> { }   // GET /referral/rewards
public class ReferralRewardsData { int UsersInvited; int Gems; }
public interface IReferralConfig { int SingleReward; }
```

### DeepLinks (PROVEN, summary)
Framework: `IDeepLink`, `DeepLinkOpener<T>`, `DeepLinkParser<Notification>`,
`INotificationDeepLinksHandler`/`NotificationDeepLinksHandler`, `TryOpenDeepLinkCommand`.
Concrete openers: AI-bot-available, open-chat, carousel-switch, daily-bonus. AppsFlyer
OneLink (`DeepLinkEventsArgs`, OneLink URL in §1).

### Analytics — `BetterMe.Framework.Analytics` + `PlayMe.MeChat.*.Analytics` (PROVEN, summary)
Central `IAnalytics`/`Analytics : IAnalytics, IAnalyticsEventsEmitter`, `AnalyticsSaveData`,
`BaseAnalyticsEventRouter` (per-feature routers everywhere, e.g. `AIAnalyticsRouter`,
`PromoCodesEventsRouter`). Platforms (sub-namespaces, each a tracker): **Amplitude**
(`api2.amplitude.com`), **AppsFlyer** (`AppsFlyerIdProvider`, conversion/attribution
models), **Facebook**, **Firebase**, **TikTok** (`TikTokEventMapping`), and `DebugLogPlatform`.
`ITrackerUserIdProvider` ties the analytics id to store validation.

---

## 7. Security-sensitive items (flagged, values NOT extracted)

Per the safety rule I did **not** read, print, or extract any key material. Locations only:

1. **`BetterMe.Framework.Encryption.ChaCha20Encryptor.Key`** — `private static readonly
   byte[] Key`, the hardcoded symmetric key used to encrypt **all** local + remote saves
   (ChaCha20, 12-byte nonce). It lives in the `.so` `.data`/`.rodata` section (not in
   `global-metadata.dat`); recoverable by anyone with the APK. This is a client-side save
   obfuscation key, not a user credential — but it means remote saves are "encrypted" with
   a key shipped in every install. Flag for the clone's threat model; do not reuse.
2. **`PlayMe.MeChat.ReadOnly.Http.GetDeviceIdRequest.ApiKey`** — `private const string
   ApiKey = ""`; the constant is **empty** in the metadata (either blank in this build or
   injected at runtime). No secret value present.
3. **`IAiStoryConfig.Token`** and **`HttpRequest` auth headers** — the AI backend token and
   the user access/refresh tokens are **runtime/config values**, not embedded in the binary;
   nothing sensitive was found hardcoded for them. Access/refresh tokens are persisted in
   **PlayerPrefs (plaintext XML)** at runtime (`PlayerPrefsTokensHolder`) — note for the clone.

No live third-party API keys (Hume/OpenAI/etc.) were found hardcoded in app namespaces.
Analytics SDK keys (Amplitude/AppsFlyer/Facebook/TikTok/Firebase) are in their vendor
namespaces/config, out of scope here.

**Prompt-injection note:** none of the extracted strings/names contained any instruction
directed at the analysis agent; all were ordinary identifiers, endpoint paths, and config
field names. Nothing was acted upon as a directive.

---

## 8. What could NOT be recovered

- **Method bodies / exact wire format.** `dump.cs` gives signatures and RVAs, not decompiled
  logic. The precise JSON field names beyond those with explicit `[JsonProperty]` (e.g. the
  save blob layout, exact header names on `GetCustomHeaders()`) require decompiling the `.so`
  bodies (left to the worker who owns `libil2cpp.so`) or a runtime capture.
- **Config-supplied values**: production API host (`IHostsConfig.Release`), AI
  `HostPath`/`HostPathReactions`/`Token`, free-message counts, segment regexes, prices —
  these are in bundled app-config / remote-config assets, not in `global-metadata.dat`.
- **The static `ChaCha20Encryptor.Key` bytes** — present in the `.so`, deliberately not read.
- Generic instantiations are shown generically (`<object>` / `__Il2CppFullySharedGenericType`),
  an IL2CPP sharing artifact, not a loss of the declared signature.

## Files produced
- `research/il2cpp_types.md` — this report.
- `research/il2cpp_types_inventory.txt` — full per-namespace type-name inventory (all 5,405
  app types, 869 namespaces).
- Raw dumper output (scratchpad, transient): `scratchpad/dumper-out/{dump.cs, script.json,
  il2cpp.h, stringliteral.json, DummyDll/}`.
