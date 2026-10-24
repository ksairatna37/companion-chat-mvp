-- Companion Chat MVP — Postgres schema
-- Server-authoritative economy: wallet.gems is a cache, wallet_ledger is truth.

create extension if not exists "pgcrypto";

-- Identity & sessions -------------------------------------------------------
-- Device-ID anonymous auth (BACKEND_CONTRACT.md Appendix A). If you instead reuse
-- an existing auth system, drop these two tables and resolve user_id there.

create table if not exists users (
  id            uuid primary key default gen_random_uuid(),   -- matches uuid user_id everywhere
  device_id     text unique,
  name          text not null default 'Guest',
  allow_mature  boolean not null default false, -- server-decided age gate (never client-sent)
  created_at    timestamptz not null default now()
);

create table if not exists sessions (
  access_token    text primary key,
  user_id         uuid not null references users(id) on delete cascade,
  access_expires  timestamptz not null,
  refresh_hash    text not null,                -- sha256(refresh token) — never store the token
  refresh_expires timestamptz not null
);
create index if not exists sessions_refresh on sessions (refresh_hash);
create index if not exists sessions_user on sessions (user_id);

-- Content catalog (editable without an app release) -------------------------

create table characters (
  id             text primary key,              -- "aria"
  name           text not null,
  tagline        text not null,
  avatar_url     text not null,
  accent_color   text not null default '#F4A988',
  persona        jsonb not null,                -- see persona.sample.json: identity/voice/safety
  unlock_cost_gems integer not null default 0,  -- 0 = free
  sort_order     integer not null default 0,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now()
);

create table story_beats (
  id             text not null,                 -- "first_meeting"
  character_id   text not null references characters(id) on delete cascade,
  idx            integer not null,              -- order within the character
  opener         text,                          -- scene line shown when chat empty at this beat
  guidance       text not null,                 -- injected into the system prompt for this beat
  advance_when   jsonb not null default '{"type":"exchanges","count":6}'::jsonb,
  primary key (character_id, id)
);
create index on story_beats (character_id, idx);

-- Per-user state ------------------------------------------------------------

create table user_story (
  user_id        uuid not null,
  character_id   text not null references characters(id) on delete cascade,
  current_beat   text not null,
  beats_unlocked text[] not null default '{}',
  rel_points     integer not null default 0,    -- relationship points (level derived)
  exchanges_in_beat integer not null default 0,  -- for advance_when "exchanges"
  updated_at     timestamptz not null default now(),
  primary key (user_id, character_id)
);

create table messages (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null,
  character_id   text not null references characters(id) on delete cascade,
  role           text not null check (role in ('user','assistant')),
  text           text not null,
  beat           text,
  client_msg_id  uuid,                          -- idempotency key (user turns)
  created_at     timestamptz not null default now()
);
create index on messages (user_id, character_id, created_at desc);
-- one user turn per client_msg_id => safe retries, no double-charge
create unique index messages_idem on messages (user_id, client_msg_id)
  where client_msg_id is not null;

-- Economy -------------------------------------------------------------------

create table wallet (
  user_id          uuid primary key,
  gems             integer not null default 0 check (gems >= 0),
  last_daily_bonus date,                        -- UTC date of last claim
  updated_at       timestamptz not null default now()
);

create table wallet_ledger (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null,
  delta          integer not null,              -- + grant, - spend
  reason         text not null,                 -- 'daily_bonus' | 'chat_message' | 'signup_grant'
  ref            text,                           -- e.g. message id or client_msg_id
  created_at     timestamptz not null default now()
);
create index on wallet_ledger (user_id, created_at desc);

-- Spend/grant helper: apply a ledger entry and keep the cache in sync atomically.
-- Returns the new balance, or raises if a spend would go negative.
create or replace function wallet_apply(p_user uuid, p_delta int, p_reason text, p_ref text)
returns integer language plpgsql as $$
declare new_balance int;
begin
  insert into wallet(user_id, gems) values (p_user, 0)
    on conflict (user_id) do nothing;

  update wallet
     set gems = gems + p_delta,
         updated_at = now()
   where user_id = p_user
   returning gems into new_balance;   -- the CHECK (gems >= 0) rejects overspend

  insert into wallet_ledger(user_id, delta, reason, ref)
  values (p_user, p_delta, p_reason, p_ref);

  return new_balance;
end $$;
