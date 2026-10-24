// Typed client for the Companion Chat backend contract (spec/BACKEND_CONTRACT.md).
// Point BASE at your own server. Auth token comes from your existing auth layer.

export const BASE = process.env.EXPO_PUBLIC_API_BASE ?? 'http://localhost:8080/v1';

export type Character = {
  id: string;
  name: string;
  tagline: string;
  avatar_url: string;
  accent_color: string;
  locked: boolean;
  unlock_cost_gems: number;
};

export type Relationship = { level: number; points: number; points_to_next: number };

export type Story = {
  character_id: string;
  current_beat: string;
  beats_unlocked: string[];
  relationship: Relationship;
  opener: string;
};

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  created_at?: string;
  beat?: string;
};

export type SendResult = {
  user_message: { id: string; text: string };
  assistant_message: { id: string; text: string; beat: string };
  wallet: { gems: number };
  story: { current_beat: string; advanced: boolean; relationship: Relationship };
};

export type ExpirableToken = { token: string; expires_at: string };
export type TokenPair = { access: ExpirableToken; refresh: ExpirableToken };

export type ApiError = { code: string; message: string };

let authToken: string | null = null;
export function setAuthToken(t: string | null) {
  authToken = t;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e: ApiError = json?.error ?? { code: 'unknown', message: `HTTP ${res.status}` };
    throw Object.assign(new Error(e.message), { code: e.code, status: res.status });
  }
  return json as T;
}

// uuid without a dep — fine for a client idempotency key
function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export const api = {
  // Auth (Appendix A): device-ID anonymous auth + refresh + heartbeat.
  // device_id is a stable UUID the caller keeps in secure storage (expo-secure-store).
  auth: (deviceId: string) =>
    req<TokenPair>('/auth', { method: 'POST', body: JSON.stringify({ device_id: deviceId }) }),
  refresh: (refreshToken: string) =>
    req<TokenPair>('/auth/refresh', { method: 'POST', body: JSON.stringify({ refresh: refreshToken }) }),
  heartbeat: () => req<void>('/auth/heartbeat', { method: 'POST' }),

  listCharacters: () => req<{ characters: Character[] }>('/characters'),
  getStory: (id: string) => req<Story>(`/characters/${id}/story`),
  getMessages: (id: string, before?: string, limit = 30) =>
    req<{ messages: Message[]; next_cursor: string | null }>(
      `/chat/${id}/messages?limit=${limit}${before ? `&before=${before}` : ''}`,
    ),
  sendMessage: (id: string, text: string) =>
    req<SendResult>(`/chat/${id}/message`, {
      method: 'POST',
      body: JSON.stringify({ text, client_msg_id: uuid() }),
    }),
  getWallet: () =>
    req<{ gems: number; daily_bonus_available: boolean; next_bonus_at: string }>('/wallet'),
  claimDailyBonus: () =>
    req<{ granted: number; gems: number; next_bonus_at: string }>('/wallet/daily-bonus', {
      method: 'POST',
    }),
};
