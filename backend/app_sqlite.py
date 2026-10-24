"""
Companion Chat MVP — runnable reference server.

One self-contained Flask app on SQLite so it starts with zero external services.
Implements the full BACKEND_CONTRACT.md surface: device-ID auth + refresh + heartbeat
(Appendix A), characters/story, server-authoritative gem economy with idempotent
atomic charge + refund-on-LLM-failure (§0/§2/§3), and the server-composed AI request
(Appendix B) behind a pluggable LLM seam.

Production target is Postgres (backend/schema.sql); this SQLite build mirrors the same
invariants in Python so the logic is runnable and testable locally.

Run:  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
      python app.py            # serves on :8080, seeds the sample character

The LLM is a deterministic stub by default (no key needed). Drop in a real provider in
call_llm() — keep the key server-side only.
"""
import json
import os
import re
import secrets
import sqlite3
import hashlib
import datetime as dt
from pathlib import Path

from flask import Flask, request, g, jsonify

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).with_name("companion.db")))
GEMS_PER_MESSAGE = int(os.environ.get("GEMS_PER_MESSAGE", "1"))
DAILY_BONUS_GEMS = int(os.environ.get("DAILY_BONUS_GEMS", "25"))
SIGNUP_GRANT_GEMS = int(os.environ.get("SIGNUP_GRANT_GEMS", "30"))
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "12"))
ACCESS_TTL = dt.timedelta(hours=1)
REFRESH_TTL = dt.timedelta(days=30)

app = Flask(__name__)


# --- DB -------------------------------------------------------------------

def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        create table if not exists users(
            id text primary key, device_id text unique, name text, created_at text,
            allow_mature integer not null default 0);
        create table if not exists sessions(
            access_token text primary key, user_id text, access_expires text,
            refresh_hash text, refresh_expires text);
        create table if not exists characters(
            id text primary key, name text, tagline text, avatar_url text,
            accent_color text, persona text, unlock_cost_gems integer default 0,
            sort_order integer default 0, is_active integer default 1);
        create table if not exists user_story(
            user_id text, character_id text, current_beat text, beats_unlocked text,
            rel_points integer default 0, exchanges_in_beat integer default 0,
            primary key(user_id, character_id));
        create table if not exists messages(
            id text primary key, user_id text, character_id text, role text, text text,
            beat text, client_msg_id text, created_at text);
        create unique index if not exists messages_idem
            on messages(user_id, client_msg_id) where client_msg_id is not null;
        create table if not exists wallet(
            user_id text primary key, gems integer not null default 0, last_daily_bonus text);
        create table if not exists wallet_ledger(
            id text primary key, user_id text, delta integer, reason text, ref text, created_at text);
        """
    )
    # seed the sample character
    row = con.execute("select count(*) from characters").fetchone()[0]
    if row == 0:
        p = json.loads(Path(__file__).with_name("persona.sample.json").read_text())
        con.execute(
            "insert into characters(id,name,tagline,avatar_url,accent_color,persona,unlock_cost_gems)"
            " values(?,?,?,?,?,?,?)",
            [p["id"], p["name"], p["tagline"], p["avatar_url"], p["accent_color"],
             json.dumps(p["persona"]), p.get("unlock_cost_gems", 0)],
        )
    con.commit()
    con.close()


# --- errors ---------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.errorhandler(ApiError)
def _api_err(e):
    return jsonify({"error": {"code": e.code, "message": e.message}}), e.status


# --- auth -----------------------------------------------------------------

def _mint_tokens(con, user_id):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    a_exp, r_exp = now() + ACCESS_TTL, now() + REFRESH_TTL
    con.execute("delete from sessions where user_id=?", [user_id])
    con.execute(
        "insert into sessions(access_token,user_id,access_expires,refresh_hash,refresh_expires)"
        " values(?,?,?,?,?)",
        [access, user_id, iso(a_exp), hashlib.sha256(refresh.encode()).hexdigest(), iso(r_exp)],
    )
    return {"access": {"token": access, "expires_at": iso(a_exp)},
            "refresh": {"token": refresh, "expires_at": iso(r_exp)}}


def current_user():
    """Resolve the bearer access token to a user row, or raise 401."""
    auth = request.headers.get("Authorization", "")
    tok = auth[7:] if auth.startswith("Bearer ") else ""
    if not tok:
        raise ApiError(401, "unauthorized", "Missing bearer token.")
    s = db().execute("select * from sessions where access_token=?", [tok]).fetchone()
    if not s or s["access_expires"] < iso(now()):
        raise ApiError(401, "unauthorized", "Token expired.")
    u = db().execute("select * from users where id=?", [s["user_id"]]).fetchone()
    if not u:
        raise ApiError(401, "unauthorized", "No such user.")
    return u


@app.post("/v1/auth")
def auth():
    body = request.get_json(force=True, silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    if not device_id:
        raise ApiError(400, "validation", "device_id is required")
    con = db()
    u = con.execute("select * from users where device_id=?", [device_id]).fetchone()
    if not u:
        uid = "u_" + secrets.token_hex(8)
        con.execute("insert into users(id,device_id,name,created_at) values(?,?,?,?)",
                    [uid, device_id, "Guest", iso(now())])
        con.execute("insert into wallet(user_id,gems) values(?,?)", [uid, SIGNUP_GRANT_GEMS])
        con.execute("insert into wallet_ledger(id,user_id,delta,reason,ref,created_at) values(?,?,?,?,?,?)",
                    ["l_" + secrets.token_hex(8), uid, SIGNUP_GRANT_GEMS, "signup_grant", None, iso(now())])
    else:
        uid = u["id"]
    pair = _mint_tokens(con, uid)
    con.commit()
    return jsonify(pair)


@app.post("/v1/auth/refresh")
def refresh():
    body = request.get_json(force=True, silent=True) or {}
    tok = (body.get("refresh") or "").strip()
    h = hashlib.sha256(tok.encode()).hexdigest()
    con = db()
    s = con.execute("select * from sessions where refresh_hash=?", [h]).fetchone()
    if not s or s["refresh_expires"] < iso(now()):
        raise ApiError(401, "unauthorized", "Refresh token invalid or expired.")
    pair = _mint_tokens(con, s["user_id"])
    con.commit()
    return jsonify(pair)


@app.post("/v1/auth/heartbeat")
def heartbeat():
    current_user()
    return "", 204


# --- characters & story ---------------------------------------------------

def _persona(con, char_id):
    c = con.execute("select * from characters where id=? and is_active=1", [char_id]).fetchone()
    if not c:
        raise ApiError(404, "not_found", "character not found")
    return c, json.loads(c["persona"])


def _beats(persona):
    return sorted(persona["beats"], key=lambda b: b["idx"])


def _load_story(con, uid, char_id, persona):
    r = con.execute("select * from user_story where user_id=? and character_id=?", [uid, char_id]).fetchone()
    if r:
        return dict(r)
    first = _beats(persona)[0]["id"]
    con.execute("insert into user_story(user_id,character_id,current_beat,beats_unlocked) values(?,?,?,?)",
                [uid, char_id, first, json.dumps([first])])
    con.commit()
    return {"user_id": uid, "character_id": char_id, "current_beat": first,
            "beats_unlocked": json.dumps([first]), "rel_points": 0, "exchanges_in_beat": 0}


def _relationship(points):
    return {"level": points // 100 + 1, "points": points, "points_to_next": 100 - (points % 100)}


@app.get("/v1/characters")
def list_characters():
    current_user()
    rows = db().execute("select * from characters where is_active=1 order by sort_order").fetchall()
    return jsonify({"characters": [
        {"id": r["id"], "name": r["name"], "tagline": r["tagline"], "avatar_url": r["avatar_url"],
         "accent_color": r["accent_color"], "locked": r["unlock_cost_gems"] > 0,
         "unlock_cost_gems": r["unlock_cost_gems"]} for r in rows]})


@app.get("/v1/characters/<char_id>/story")
def get_story(char_id):
    u = current_user()
    con = db()
    c, persona = _persona(con, char_id)
    st = _load_story(con, u["id"], char_id, persona)
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    return jsonify({
        "character_id": char_id, "current_beat": st["current_beat"],
        "beats_unlocked": json.loads(st["beats_unlocked"]),
        "relationship": _relationship(st["rel_points"]),
        "opener": beat.get("opener", ""),
    })


@app.get("/v1/chat/<char_id>/messages")
def get_messages(char_id):
    u = current_user()
    limit = min(int(request.args.get("limit", 30)), 100)
    rows = db().execute(
        "select id,role,text,beat,created_at from messages where user_id=? and character_id=?"
        " order by created_at desc limit ?", [u["id"], char_id, limit]).fetchall()
    return jsonify({"messages": [dict(r) for r in rows], "next_cursor": None})


# --- wallet ---------------------------------------------------------------

def _balance(con, uid):
    r = con.execute("select gems from wallet where user_id=?", [uid]).fetchone()
    return r["gems"] if r else 0


def _apply(con, uid, delta, reason, ref):
    """Mirror of schema.sql wallet_apply(): adjust balance + append ledger. Rejects overspend."""
    bal = _balance(con, uid)
    new = bal + delta
    if new < 0:
        raise ApiError(402, "insufficient_gems", "Not enough gems.")
    con.execute("insert or ignore into wallet(user_id,gems) values(?,0)", [uid])
    con.execute("update wallet set gems=? where user_id=?", [new, uid])
    con.execute("insert into wallet_ledger(id,user_id,delta,reason,ref,created_at) values(?,?,?,?,?,?)",
                ["l_" + secrets.token_hex(8), uid, delta, reason, ref, iso(now())])
    return new


@app.get("/v1/wallet")
def get_wallet():
    u = current_user()
    w = db().execute("select * from wallet where user_id=?", [u["id"]]).fetchone()
    today = now().date().isoformat()
    last = w["last_daily_bonus"] if w else None
    nxt = (now().date() + dt.timedelta(days=1))
    return jsonify({"gems": w["gems"] if w else 0,
                    "daily_bonus_available": last != today,
                    "next_bonus_at": dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")})


@app.post("/v1/wallet/daily-bonus")
def daily_bonus():
    u = current_user()
    con = db()
    con.execute("begin")
    today = now().date().isoformat()
    w = con.execute("select * from wallet where user_id=?", [u["id"]]).fetchone()
    if w and w["last_daily_bonus"] == today:
        con.execute("rollback")
        raise ApiError(409, "already_claimed", "Daily bonus already claimed today.")
    bal = _apply(con, u["id"], DAILY_BONUS_GEMS, "daily_bonus", today)
    con.execute("update wallet set last_daily_bonus=? where user_id=?", [today, u["id"]])
    con.commit()
    nxt = now().date() + dt.timedelta(days=1)
    return jsonify({"granted": DAILY_BONUS_GEMS, "gems": bal,
                    "next_bonus_at": dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")})


# --- LLM seam -------------------------------------------------------------

def call_llm(system: str, context: list, user_text: str) -> str:
    """Compose is done by the caller; this just turns (system, context, text) into a reply.

    Default: a deterministic stub so the server runs with no API key. Replace the body
    with your own provider call (OpenAI/Anthropic/etc). KEEP THE KEY SERVER-SIDE.
    """
    if os.environ.get("LLM_API_KEY"):
        # Seam for a real provider. Example shape (left unwired on purpose):
        #   return my_provider.chat(system=system, messages=context + [{"role":"user","content":user_text}])
        pass
    # --- stub: short, persona-flavoured, deterministic -----------------
    persona_line = system.splitlines()[0] if system else "You are a companion."
    seed = int(hashlib.sha256(user_text.encode()).hexdigest(), 16)
    openers = ["Mm.", "Hey.", "Funny you ask.", "Okay, listen.", "Honestly?"]
    reply = f"{openers[seed % len(openers)]} {user_text.strip().rstrip('?.!')}… I think about that more than I let on."
    if "[[signal]]" in user_text:
        reply += " [[beat_complete]]"
    return reply


def _compose_ai_payload(con, u, persona, beat, history, user_text):
    """Appendix B shape — built server-side; persona_facts never come from the client."""
    persona_facts = "\n\n".join([persona["system"], persona["voice"], persona["safety"], beat["guidance"]])
    return {
        "user_profile": {"id": u["id"], "name": u["name"]},
        "bot_profile": {"name": persona.get("name", "Companion"), "persona_facts": persona_facts},
        "context": [{"turn": "user" if m["role"] == "user" else "bot", "message": m["text"]}
                    for m in history] + [{"turn": "user", "message": user_text}],
        "chat_settings": {"allow_mature": bool(u["allow_mature"])},  # server-decided, not client
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
    prior = con.execute("select id,beat from messages where user_id=? and client_msg_id=?",
                        [u["id"], cmid]).fetchone()
    if prior:
        asst = con.execute(
            "select * from messages where user_id=? and character_id=? and role='assistant'"
            " and created_at>=(select created_at from messages where id=?) order by created_at limit 1",
            [u["id"], char_id, prior["id"]]).fetchone()
        st = con.execute("select * from user_story where user_id=? and character_id=?",
                         [u["id"], char_id]).fetchone()
        return jsonify({
            "user_message": {"id": prior["id"], "text": text},
            "assistant_message": {"id": asst["id"], "text": asst["text"], "beat": asst["beat"]} if asst else None,
            "wallet": {"gems": _balance(con, u["id"])},
            "story": {"current_beat": st["current_beat"], "advanced": False,
                      "relationship": _relationship(st["rel_points"])},
            "replayed": True,
        })

    c, persona = _persona(con, char_id)
    st = _load_story(con, u["id"], char_id, persona)
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    history = list(reversed(con.execute(
        "select role,text from messages where user_id=? and character_id=? order by created_at desc limit ?",
        [u["id"], char_id, HISTORY_TURNS * 2]).fetchall()))

    # (2)+(3) charge BEFORE the LLM, atomically; store the user turn
    con.execute("begin")
    _apply(con, u["id"], -GEMS_PER_MESSAGE, "chat_message", cmid)  # raises 402 if short
    user_id_msg = "m_" + secrets.token_hex(8)
    con.execute("insert into messages(id,user_id,character_id,role,text,client_msg_id,created_at)"
                " values(?,?,?,?,?,?,?)",
                [user_id_msg, u["id"], char_id, "user", text, cmid, iso(now())])
    con.commit()

    # (4) LLM OUTSIDE the charge txn; refund + 503 on failure
    try:
        payload = _compose_ai_payload(con, u, persona, beat, history, text)
        system = payload["bot_profile"]["persona_facts"]
        reply = call_llm(system, payload["context"], text)
    except Exception:
        con.execute("begin")
        _apply(con, u["id"], GEMS_PER_MESSAGE, "chat_refund", cmid)
        con.commit()
        raise ApiError(503, "llm_unavailable", "The companion is unavailable. You were not charged.")

    # strip the advance sentinel if this is a model_signal beat
    advanced = False
    if beat.get("advance_when", {}).get("type") == "model_signal" and "[[beat_complete]]" in reply:
        reply = reply.replace("[[beat_complete]]", "").strip()
        advanced = True

    con.execute("begin")
    asst_id = "m_" + secrets.token_hex(8)
    con.execute("insert into messages(id,user_id,character_id,role,text,beat,created_at) values(?,?,?,?,?,?,?)",
                [asst_id, u["id"], char_id, "assistant", reply, st["current_beat"], iso(now())])
    new_story = _advance(con, u["id"], char_id, persona, st, advanced)
    con.commit()

    return jsonify({
        "user_message": {"id": user_id_msg, "text": text},
        "assistant_message": {"id": asst_id, "text": reply, "beat": st["current_beat"]},
        "wallet": {"gems": _balance(con, u["id"])},
        "story": {"current_beat": new_story["current_beat"],
                  "advanced": new_story["current_beat"] != st["current_beat"],
                  "relationship": _relationship(new_story["rel_points"])},
    })


def _advance(con, uid, char_id, persona, st, signalled):
    beat = next(b for b in persona["beats"] if b["id"] == st["current_beat"])
    rule = beat.get("advance_when", {})
    exchanges = st["exchanges_in_beat"] + 1
    should = signalled or (rule.get("type") == "exchanges" and exchanges >= rule.get("count", 6))
    beats = _beats(persona)
    i = next(k for k, b in enumerate(beats) if b["id"] == st["current_beat"])
    nxt = beats[i + 1]["id"] if should and i + 1 < len(beats) else st["current_beat"]
    rel = st["rel_points"] + 5
    unlocked = set(json.loads(st["beats_unlocked"])) | {nxt}
    con.execute("update user_story set current_beat=?, exchanges_in_beat=?, rel_points=?, beats_unlocked=?"
                " where user_id=? and character_id=?",
                [nxt, 0 if should else exchanges, rel, json.dumps(sorted(unlocked)), uid, char_id])
    return {"current_beat": nxt, "rel_points": rel}


@app.get("/v1/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
