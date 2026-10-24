// Device-ID + token lifecycle (BACKEND_CONTRACT.md Appendix A).
// Owns: a stable device_id, the access/refresh pair, refresh-before-expiry, and
// refresh-on-401. Tokens live in Secure storage, never plaintext (the lesson from
// the teardown — the analysed app kept them in plaintext PlayerPrefs).
//
// Requires: expo-secure-store. In a bare RN app swap for react-native-keychain.

import * as SecureStore from 'expo-secure-store';
import { api, setAuthToken, type TokenPair } from './api';

const K_DEVICE = 'cc.device_id';
const K_ACCESS = 'cc.access';
const K_ACCESS_EXP = 'cc.access_exp';
const K_REFRESH = 'cc.refresh';

function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

async function deviceId(): Promise<string> {
  let id = await SecureStore.getItemAsync(K_DEVICE);
  if (!id) {
    id = uuid();
    await SecureStore.setItemAsync(K_DEVICE, id);
  }
  return id;
}

async function store(pair: TokenPair) {
  await SecureStore.setItemAsync(K_ACCESS, pair.access.token);
  await SecureStore.setItemAsync(K_ACCESS_EXP, pair.access.expires_at);
  await SecureStore.setItemAsync(K_REFRESH, pair.refresh.token);
  setAuthToken(pair.access.token);
}

/** Call once at app start. Restores a session via refresh, or creates one via device-id. */
export async function bootstrapSession(): Promise<void> {
  const refresh = await SecureStore.getItemAsync(K_REFRESH);
  if (refresh) {
    try {
      await store(await api.refresh(refresh));
      return;
    } catch {
      // refresh expired/invalid — fall through to a fresh device-id auth
    }
  }
  await store(await api.auth(await deviceId()));
}

/** Ensure the access token is valid now (refresh if within 60s of expiry). */
export async function ensureFreshAccess(): Promise<void> {
  const exp = await SecureStore.getItemAsync(K_ACCESS_EXP);
  const refresh = await SecureStore.getItemAsync(K_REFRESH);
  if (!exp || !refresh) return bootstrapSession();
  if (Date.parse(exp) - Date.now() < 60_000) {
    try {
      await store(await api.refresh(refresh));
    } catch {
      await bootstrapSession();
    }
  }
}

/**
 * Run an API call, and on a 401 refresh once and retry (Appendix A's reactive path).
 * Wrap your api.* calls with this:  const list = await withAuthRetry(() => api.listCharacters());
 */
export async function withAuthRetry<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (e: any) {
    if (e?.status !== 401) throw e;
    const refresh = await SecureStore.getItemAsync(K_REFRESH);
    if (refresh) {
      try {
        await store(await api.refresh(refresh));
      } catch {
        await bootstrapSession();
      }
    } else {
      await bootstrapSession();
    }
    return call();
  }
}

export async function signOut(): Promise<void> {
  await Promise.all([K_ACCESS, K_ACCESS_EXP, K_REFRESH].map((k) => SecureStore.deleteItemAsync(k)));
  setAuthToken(null);
}
