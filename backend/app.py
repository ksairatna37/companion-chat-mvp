"""
Companion Chat MVP — Postgres server (canonical / production target).

Same contract as app_sqlite.py, ported to Postgres via psycopg3 against backend/schema.sql
(which owns the `wallet_apply()` function used for every balance change). Device-ID auth
+ refresh + heartbeat (Appendix A), server-authoritative gem economy with idempotent
atomic charge + refund-on-LLM-failure (§0/§2/§3), server-composed AI request (Appendix B)
behind the pluggable call_llm() seam.

Run:
  export DATABASE_URL=postgresql://localhost/companion     # your own Postgres (Supabase/Neon/local)
  . .venv/bin/activate && pip install -r requirements.txt
  python app.py                                             # applies schema.sql + seeds on first run

app_sqlite.py remains the zero-dependency local quickstart. This file is what you deploy.
The LLM is a deterministic stub until you wire a provider in call_llm() (key stays server-side).
"""
import os
import json
import secrets
import hashlib
import datetime as dt
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg import errors as pgerr
from flask import Flask, request, g, jsonify

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///companion")
GEMS_PER_MESSAGE = int(os.environ.get("GEMS_PER_MESSAGE", "1"))
DAILY_BONUS_GEMS = int(os.environ.get("DAILY_BONUS_GEMS", "25"))
SIGNUP_GRANT_GEMS = int(os.environ.get("SIGNUP_GRANT_GEMS", "30"))
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "12"))
ACCESS_TTL_HOURS = 1
REFRESH_TTL_DAYS = 30

app = Flask(__name__)


# --- DB -------------------------------------------------------------------

def db():
    if "db" not in g:
        g.db = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def one(cur):
    r = cur.fetchone()
    return r


def init_db():
    """Apply schema.sql (idempotent) and seed the sample character on first run."""
    schema = Path(__file__).with_name("schema.sql").read_text()
    with psycopg.connect(DATABASE_URL, autocommit=True) as con:
        # no params -> simple-query protocol -> multiple statements allowed
        con.execute(schema)
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("select count(*) as n from characters")
            if cur.fetchone()["n"] == 0:
                p = json.loads(Path(__file__).with_name("persona.sample.json").read_text())
                cur.execute(
                    "insert into characters(id,name,tagline,avatar_url,accent_color,persona,unlock_cost_gems)"
                    " values(%s,%s,%s,%s,%s,%s,%s)",
                    [p["id"], p["name"], p["tagline"], p["avatar_url"], p["accent_color"],
                     json.dumps(p["persona"]), p.get("unlock_cost_gems", 0)],
                )


# --- errors ---------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.errorhandler(ApiError)
def _api_err(e):
    return jsonify({"error": {"code": e.code, "message": e.message}}), e.status


# --- wallet (uses schema.sql wallet_apply) --------------------------------

def wallet_apply(cur, uid, delta, reason, ref):
    """Call the DB function; translate its CHECK(gems>=0) violation to a 402.
    Must be called inside a transaction so a rejected spend rolls back cleanly."""
    try:
        cur.execute("select wallet_apply(%s,%s,%s,%s) as bal", [uid, delta, reason, ref])
        return cur.fetchone()["bal"]
    except pgerr.CheckViolation:
        raise ApiError(402, "insufficient_gems", "Not enough gems.")


def balance(con, uid):
    with con.cursor() as cur:
        cur.execute("select gems from wallet where user_id=%s", [uid])
        r = cur.fetchone()
        return r["gems"] if r else 0


# --- auth -----------------------------------------------------------------

def _mint_tokens(cur, uid):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    cur.execute("delete from sessions where user_id=%s", [uid])
    cur.execute(
        "insert into sessions(access_token,user_id,access_expires,refresh_hash,refresh_expires)"
        " values(%s,%s, now()+(%s||' hours')::interval, %s, now()+(%s||' days')::interval)"
        " returning access_expires, refresh_expires",
        [access, uid, ACCESS_TTL_HOURS, hashlib.sha256(refresh.encode()).hexdigest(), REFRESH_TTL_DAYS],
    )
    exp = cur.fetchone()
    return {"access": {"token": access, "expires_at": _iso(exp["access_expires"])},
            "refresh": {"token": refresh, "expires_at": _iso(exp["refresh_expires"])}}


def _iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_user():
    auth = request.headers.get("Authorization", "")
    tok = auth[7:] if auth.startswith("Bearer ") else ""
    if not tok:
        raise ApiError(401, "unauthorized", "Missing bearer token.")
    with db().cursor() as cur:
        cur.execute(
            "select u.id::text as id, u.name, u.allow_mature from sessions s join users u on u.id=s.user_id"
            " where s.access_token=%s and s.access_expires > now()", [tok])
        u = cur.fetchone()
    if not u:
        raise ApiError(401, "unauthorized", "Token missing, invalid, or expired.")
    return u


@app.post("/v1/auth")
def auth():
    body = request.get_json(force=True, silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    if not device_id:
        raise ApiError(400, "validation", "device_id is required")
    con = db()
    with con.transaction(), con.cursor() as cur:
        cur.execute("select id::text from users where device_id=%s", [device_id])
        row = cur.fetchone()
        if row:
            uid = row["id"]
        else:
            cur.execute("insert into users(device_id) values(%s) returning id::text", [device_id])
            uid = cur.fetchone()["id"]
            wallet_apply(cur, uid, SIGNUP_GRANT_GEMS, "signup_grant", None)
        pair = _mint_tokens(cur, uid)
    return jsonify(pair)


@app.post("/v1/auth/refresh")
def refresh():
    body = request.get_json(force=True, silent=True) or {}
    h = hashlib.sha256((body.get("refresh") or "").encode()).hexdigest()
    con = db()
    with con.transaction(), con.cursor() as cur:
        cur.execute("select user_id from sessions where refresh_hash=%s and refresh_expires > now()", [h])
        s = cur.fetchone()
        if not s:
            raise ApiError(401, "unauthorized", "Refresh token invalid or expired.")
        pair = _mint_tokens(cur, s["user_id"])
    return jsonify(pair)


@app.post("/v1/auth/heartbeat")
def heartbeat():
    current_user()
    return "", 204


# --- characters & story ---------------------------------------------------

def _persona(con, char_id):
    with con.cursor() as cur:
        cur.execute("select * from characters where id=%s and is_active", [char_id])
        c = cur.fetchone()
    if not c:
        raise ApiError(404, "not_found", "character not found")
    persona = c["persona"] if isinstance(c["persona"], dict) else json.loads(c["persona"])
    return c, persona


def _beats(persona):
    return sorted(persona["beats"], key=lambda b: b["idx"])


def _load_story(con, uid, char_id, persona):
    with con.cursor() as cur:
        cur.execute("select * from user_story where user_id=%s and character_id=%s", [uid, char_id])
        r = cur.fetchone()
        if r:
            return r
        first = _beats(persona)[0]["id"]
        cur.execute(
            "insert into user_story(user_id,character_id,current_beat,beats_unlocked)"
            " values(%s,%s,%s,%s) returning *", [uid, char_id, first, [first]])
        return cur.fetchone()


def _relationship(points):
    return {"level": points // 100 + 1, "points": points, "points_to_next": 100 - (points % 100)}


@app.get("/v1/characters")
def list_characters():
    current_user()
    with db().cursor() as cur:
        cur.execute("select * from characters where is_active order by sort_order")
        rows = cur.fetchall()
    return jsonify({"characters": [
        {"id": r["id"], "name": r["name"], "tagline": r["tagline"], "avatar_url": r["avatar_url"],
         "accent_color": r["accent_color"], "locked": r["unlock_cost_gems"] > 0,
         "unlock_cost_gems": r["unlock_cost_gems"]} for r in rows]})


@app.get("/v1/characters/<char_id>/story")
def get_story(char_id):
    u = current_user()
    con = db()
    _, persona = _persona(con, char_id)
    st = _load_story(con, u["id"], char_id, persona)
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    return jsonify({
        "character_id": char_id, "current_beat": st["current_beat"],
        "beats_unlocked": list(st["beats_unlocked"]),
        "relationship": _relationship(st["rel_points"]), "opener": beat.get("opener", ""),
    })


@app.get("/v1/chat/<char_id>/messages")
def get_messages(char_id):
    u = current_user()
    limit = min(int(request.args.get("limit", 30)), 100)
    with db().cursor() as cur:
        cur.execute(
            "select id::text, role, text, beat, created_at from messages"
            " where user_id=%s and character_id=%s order by created_at desc limit %s",
            [u["id"], char_id, limit])
        rows = cur.fetchall()
    return jsonify({"messages": [
        {"id": r["id"], "role": r["role"], "text": r["text"], "beat": r["beat"],
         "created_at": _iso(r["created_at"])} for r in rows], "next_cursor": None})


# --- wallet endpoints -----------------------------------------------------

@app.get("/v1/wallet")
def get_wallet():
    u = current_user()
    with db().cursor() as cur:
        cur.execute("select gems, last_daily_bonus, (last_daily_bonus is distinct from current_date)"
                    " as avail from wallet where user_id=%s", [u["id"]])
        w = cur.fetchone()
    return jsonify({"gems": w["gems"] if w else 0,
                    "daily_bonus_available": (w["avail"] if w else True),
                    "next_bonus_at": _tomorrow_utc()})


@app.post("/v1/wallet/daily-bonus")
def daily_bonus():
    u = current_user()
    con = db()
    with con.transaction(), con.cursor() as cur:
        cur.execute("select last_daily_bonus from wallet where user_id=%s for update", [u["id"]])
        w = cur.fetchone()
        if w and w["last_daily_bonus"] == dt.date.today():
            raise ApiError(409, "already_claimed", "Daily bonus already claimed today.")
        bal = wallet_apply(cur, u["id"], DAILY_BONUS_GEMS, "daily_bonus", dt.date.today().isoformat())
        cur.execute("update wallet set last_daily_bonus=current_date where user_id=%s", [u["id"]])
    return jsonify({"granted": DAILY_BONUS_GEMS, "gems": bal, "next_bonus_at": _tomorrow_utc()})


def _tomorrow_utc():
    d = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")


# --- LLM seam -------------------------------------------------------------

def call_llm(system: str, context: list, user_text: str) -> str:
    """(system, context, user_text) -> reply. Deterministic stub until you wire a provider.
    Replace the body with your own provider call; KEEP THE KEY SERVER-SIDE (LLM_API_KEY)."""
    if os.environ.get("LLM_API_KEY"):
        pass  # seam: provider.chat(system=system, messages=context + [{"role":"user","content":user_text}])
    seed = int(hashlib.sha256(user_text.encode()).hexdigest(), 16)
    openers = ["Mm.", "Hey.", "Funny you ask.", "Okay, listen.", "Honestly?"]
    reply = f"{openers[seed % len(openers)]} {user_text.strip().rstrip('?.!')}… I think about that more than I let on."
    if "[[signal]]" in user_text:
        reply += " [[beat_complete]]"
    return reply


def _compose_ai_payload(u, persona, beat, history, user_text):
    persona_facts = "\n\n".join([persona["system"], persona["voice"], persona["safety"], beat["guidance"]])
    return {
        "user_profile": {"id": u["id"], "name": u["name"]},
        "bot_profile": {"name": persona.get("name", "Companion"), "persona_facts": persona_facts},
        "context": [{"turn": "user" if m["role"] == "user" else "bot", "message": m["text"]}
                    for m in history] + [{"turn": "user", "message": user_text}],
        "chat_settings": {"allow_mature": bool(u["allow_mature"])},  # server-decided, never client
    }


# --- the core endpoint ----------------------------------------------------

@app.post("/v1/chat/<char_id>/message")
def send_message(char_id):
    u = current_user()
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    cmid = (body.get("client_msg_id") or "").strip()
    if not text:
        raise ApiError(400, "validation", "text is required")
    if not cmid:
        raise ApiError(400, "validation", "client_msg_id is required")
    con = db()

    # (1) idempotency: replay a prior result for this client_msg_id, no re-charge
    with con.cursor() as cur:
        cur.execute("select id::text, created_at from messages where user_id=%s and client_msg_id=%s",
                    [u["id"], cmid])
        prior = cur.fetchone()
    if prior:
        with con.cursor() as cur:
            cur.execute(
                "select id::text, text, beat from messages where user_id=%s and character_id=%s"
                " and role='assistant' and created_at>=%s order by created_at limit 1",
                [u["id"], char_id, prior["created_at"]])
            asst = cur.fetchone()
            cur.execute("select current_beat, rel_points from user_story where user_id=%s and character_id=%s",
                        [u["id"], char_id])
            st = cur.fetchone()
        return jsonify({
            "user_message": {"id": prior["id"], "text": text},
            "assistant_message": ({"id": asst["id"], "text": asst["text"], "beat": asst["beat"]}
                                  if asst else None),
            "wallet": {"gems": balance(con, u["id"])},
            "story": {"current_beat": st["current_beat"], "advanced": False,
                      "relationship": _relationship(st["rel_points"])},
            "replayed": True,
        })

    _, persona = _persona(con, char_id)
    st = _load_story(con, u["id"], char_id, persona)
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    with con.cursor() as cur:
        cur.execute("select role, text from messages where user_id=%s and character_id=%s"
                    " order by created_at desc limit %s", [u["id"], char_id, HISTORY_TURNS * 2])
        history = list(reversed(cur.fetchall()))

    # (2)+(3) charge BEFORE the LLM, atomically; store the user turn
    with con.transaction(), con.cursor() as cur:
        wallet_apply(cur, u["id"], -GEMS_PER_MESSAGE, "chat_message", cmid)  # raises 402 if short
        cur.execute("insert into messages(user_id,character_id,role,text,client_msg_id)"
                    " values(%s,%s,'user',%s,%s) returning id::text",
                    [u["id"], char_id, text, cmid])
        user_msg_id = cur.fetchone()["id"]

    # (4) LLM OUTSIDE the charge txn; refund + 503 on failure
    try:
        payload = _compose_ai_payload(u, persona, beat, history, text)
        reply = call_llm(payload["bot_profile"]["persona_facts"], payload["context"], text)
    except Exception:
        with con.transaction(), con.cursor() as cur:
            wallet_apply(cur, u["id"], GEMS_PER_MESSAGE, "chat_refund", cmid)
        raise ApiError(503, "llm_unavailable", "The companion is unavailable. You were not charged.")

    advanced = False
    if beat.get("advance_when", {}).get("type") == "model_signal" and "[[beat_complete]]" in reply:
        reply = reply.replace("[[beat_complete]]", "").strip()
        advanced = True

    with con.transaction(), con.cursor() as cur:
        cur.execute("insert into messages(user_id,character_id,role,text,beat)"
                    " values(%s,%s,'assistant',%s,%s) returning id::text",
                    [u["id"], char_id, reply, st["current_beat"]])
        asst_id = cur.fetchone()["id"]
        new_story = _advance(cur, u["id"], char_id, persona, st, advanced)

    return jsonify({
        "user_message": {"id": user_msg_id, "text": text},
        "assistant_message": {"id": asst_id, "text": reply, "beat": st["current_beat"]},
        "wallet": {"gems": balance(con, u["id"])},
        "story": {"current_beat": new_story["current_beat"],
                  "advanced": new_story["current_beat"] != st["current_beat"],
                  "relationship": _relationship(new_story["rel_points"])},
    })


def _advance(cur, uid, char_id, persona, st, signalled):
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    rule = beat.get("advance_when", {})
    exchanges = st["exchanges_in_beat"] + 1
    should = signalled or (rule.get("type") == "exchanges" and exchanges >= rule.get("count", 6))
    beats = _beats(persona)
    i = next(k for k, b in enumerate(beats) if b["id"] == st["current_beat"])
    nxt = beats[i + 1]["id"] if should and i + 1 < len(beats) else st["current_beat"]
    rel = st["rel_points"] + 5
    cur.execute(
        "update user_story set current_beat=%s, exchanges_in_beat=%s, rel_points=%s,"
        " beats_unlocked=(select array(select distinct unnest(beats_unlocked || %s::text[])))"
        " where user_id=%s and character_id=%s",
        [nxt, 0 if should else exchanges, rel, [nxt], uid, char_id])
    return {"current_beat": nxt, "rel_points": rel}


@app.get("/v1/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
