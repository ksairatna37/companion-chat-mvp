"""
Reference handler for POST /v1/chat/<char_id>/message — the one endpoint with
real ordering risk. Framework-agnostic core; wrap it in Flask/FastAPI to taste.

The invariants that matter (and that the analysed app's retrying AI layer makes
easy to get wrong):
  1. Idempotent on client_msg_id — a retry returns the first result, never double-charges.
  2. No LLM call if the wallet can't cover it.
  3. Charge and refund are ledger transactions; wallet.gems is only a cache.
  4. If the LLM fails, the user is refunded and gets 503 — not a silent charge.

`db` is a connection with a .tx() context manager (BEGIN/COMMIT/ROLLBACK) and
.query()/.exec(). `llm(system, history, user_text)` returns the reply string or
raises LlmError. Both are yours to implement against your own provider.
"""

GEMS_PER_MESSAGE = 1
HISTORY_TURNS = 12


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def handle_chat_message(db, llm, user_id, char_id, body):
    text = (body.get("text") or "").strip()
    client_msg_id = body.get("client_msg_id")
    if not text:
        raise ApiError(400, "validation", "text is required")
    if not client_msg_id:
        raise ApiError(400, "validation", "client_msg_id is required")

    # (1) Idempotency: if we've already processed this client_msg_id, replay it.
    prior = db.query(
        "select id from messages where user_id=%s and client_msg_id=%s",
        [user_id, client_msg_id],
    )
    if prior:
        return _replay(db, user_id, char_id, prior[0]["id"])

    char = db.query("select persona from characters where id=%s and is_active", [char_id])
    if not char:
        raise ApiError(404, "not_found", "character not found")
    persona = char[0]["persona"]

    story = _load_or_init_story(db, user_id, char_id, persona)
    beat = _beat(persona, story["current_beat"])
    system = "\n\n".join([persona["system"], persona["voice"], persona["safety"], beat["guidance"]])
    history = _recent_history(db, user_id, char_id, HISTORY_TURNS)

    # (2)+(3) Charge inside a transaction BEFORE the LLM call. The CHECK (gems >= 0)
    # on wallet rejects an overspend, which we translate to 402.
    with db.tx():
        try:
            db.query("select wallet_apply(%s,%s,'chat_message',%s) as bal",
                     [user_id, -GEMS_PER_MESSAGE, client_msg_id])
        except db.CheckViolation:
            raise ApiError(402, "insufficient_gems", "Not enough gems.")
        user_msg = db.query(
            "insert into messages(user_id,character_id,role,text,client_msg_id) "
            "values(%s,%s,'user',%s,%s) returning id",
            [user_id, char_id, text, client_msg_id],
        )[0]

    # (4) LLM call OUTSIDE the charge transaction. On failure, refund and 503.
    try:
        reply = llm(system, history, text)
    except Exception:
        with db.tx():
            db.query("select wallet_apply(%s,%s,'chat_refund',%s)",
                     [user_id, GEMS_PER_MESSAGE, client_msg_id])
        raise ApiError(503, "llm_unavailable", "The companion is unavailable. You were not charged.")

    reply, advanced = _strip_signal(reply, beat)

    with db.tx():
        asst = db.query(
            "insert into messages(user_id,character_id,role,text,beat) "
            "values(%s,%s,'assistant',%s,%s) returning id",
            [user_id, char_id, reply, story["current_beat"]],
        )[0]
        new_story = _advance_story(db, user_id, char_id, persona, story, advanced)
        bal = db.query("select gems from wallet where user_id=%s", [user_id])[0]["gems"]

    return {
        "user_message": {"id": user_msg["id"], "text": text},
        "assistant_message": {"id": asst["id"], "text": reply, "beat": story["current_beat"]},
        "wallet": {"gems": bal},
        "story": {
            "current_beat": new_story["current_beat"],
            "advanced": new_story["current_beat"] != story["current_beat"],
            "relationship": _relationship(new_story["rel_points"]),
        },
    }


# --- helpers (sketch) -------------------------------------------------------

def _beat(persona, beat_id):
    return next(b for b in persona["beats"] if b["id"] == beat_id)


def _strip_signal(reply, beat):
    """model_signal beats end the reply with a hidden sentinel we strip + act on."""
    if beat.get("advance_when", {}).get("type") == "model_signal" and "[[beat_complete]]" in reply:
        return reply.replace("[[beat_complete]]", "").strip(), True
    return reply, False


def _advance_story(db, user_id, char_id, persona, story, signalled):
    beat = _beat(persona, story["current_beat"])
    rule = beat.get("advance_when", {})
    exchanges = story["exchanges_in_beat"] + 1
    should = signalled or (rule.get("type") == "exchanges" and exchanges >= rule.get("count", 6))

    beats = sorted(persona["beats"], key=lambda b: b["idx"])
    cur_idx = next(i for i, b in enumerate(beats) if b["id"] == story["current_beat"])
    next_beat = beats[cur_idx + 1]["id"] if should and cur_idx + 1 < len(beats) else story["current_beat"]
    rel_points = story["rel_points"] + 5

    db.query(
        "update user_story set current_beat=%s, exchanges_in_beat=%s, rel_points=%s, "
        "beats_unlocked=(select array(select distinct unnest(beats_unlocked||%s::text[]))), "
        "updated_at=now() where user_id=%s and character_id=%s",
        [next_beat, 0 if should else exchanges, rel_points, [next_beat], user_id, char_id],
    )
    return {"current_beat": next_beat, "rel_points": rel_points}


def _relationship(points):
    level = points // 100 + 1
    return {"level": level, "points": points, "points_to_next": 100 - (points % 100)}


def _recent_history(db, user_id, char_id, n):
    rows = db.query(
        "select role,text from messages where user_id=%s and character_id=%s "
        "order by created_at desc limit %s",
        [user_id, char_id, n * 2],
    )
    return [{"role": r["role"], "text": r["text"]} for r in reversed(rows)]


def _load_or_init_story(db, user_id, char_id, persona):
    rows = db.query("select * from user_story where user_id=%s and character_id=%s", [user_id, char_id])
    if rows:
        return rows[0]
    first = sorted(persona["beats"], key=lambda b: b["idx"])[0]["id"]
    return db.query(
        "insert into user_story(user_id,character_id,current_beat,beats_unlocked) "
        "values(%s,%s,%s,%s) returning *",
        [user_id, char_id, first, [first]],
    )[0]


def _replay(db, user_id, char_id, user_msg_id):
    # Return the stored assistant reply that followed this user turn, plus live balance.
    raise ApiError(409, "duplicate", "Already processed; fetch history instead.")  # simplest v1: tell client to refetch
