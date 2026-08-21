"""The archive: every event, everyone who came, and everything they did.

The live room stays in memory — a vote must never wait on a disk. This module
takes a copy of what happened and writes it down, so that RESET clears the room
without clearing the record, and so questions that span events ("founders who
have been twice and raised a hand") become one query instead of a script.

SQLite via the standard library, so the zero-dependency rule still holds. One
file on the data volume.

Two halves that never touch:

  · `people` — name, email, occupation. Who to contact, and nothing else.
  · `attendees` + `responses` — what a room thought, carrying occupation and
    vibe so it can be broken down, and nothing that says who anyone was.

There is deliberately no join between them. An attendee is identified by a hash
of the phone's token *and the event*, so answers group within one evening — you
can ask whether people who arrived sceptical shifted more than people who
arrived fired up — but the same phone at the next event is a different, unlinkable
row. The raw token is never stored.

The consequence is worth stating plainly, because it cuts both ways: once an
evening is over, nobody — including us — can find out what any named person
said. That is the point. It also means a deletion request can only remove the
contact record, since there is nothing personal left in the answers to remove.
"""

import hashlib
import hmac
import json
import os
import secrets
import queue
import sqlite3
import threading
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- who to contact. Never joined to anything in the response half.
CREATE TABLE IF NOT EXISTS people (
  id          INTEGER PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name        TEXT NOT NULL DEFAULT '',
  occupation  TEXT NOT NULL DEFAULT '',
  first_seen  REAL,
  last_seen   REAL,
  suppressed  INTEGER NOT NULL DEFAULT 0
);

-- one run of an event in a room: "the night of the 12th"
CREATE TABLE IF NOT EXISTS sessions (
  id          INTEGER PRIMARY KEY,
  room_code   TEXT NOT NULL,
  event_id    TEXT NOT NULL DEFAULT '',
  event_name  TEXT NOT NULL DEFAULT '',
  opened_at   REAL,
  closed_at   REAL,
  -- a reset means "that was a rehearsal": kept, but out of the reports
  discarded   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_room ON sessions(room_code, discarded, closed_at);

-- One anonymous person-shaped thing in one session. `anon` is a hash of the
-- phone's token and the session, so it groups an evening's answers together and
-- is useless anywhere else. No name, no email, no link to `people`.
CREATE TABLE IF NOT EXISTS attendees (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  anon          TEXT NOT NULL,
  occupation    TEXT NOT NULL DEFAULT '',
  vibe          TEXT NOT NULL DEFAULT '',
  checked_in_at REAL,
  UNIQUE(session_id, anon)
);

CREATE TABLE IF NOT EXISTS responses (
  id                   INTEGER PRIMARY KEY,
  attendee_id          INTEGER NOT NULL REFERENCES attendees(id) ON DELETE CASCADE,
  session_id           INTEGER NOT NULL,
  kind                 TEXT NOT NULL,
  topic_index          INTEGER,
  topic_question       TEXT NOT NULL DEFAULT '',
  interaction_question TEXT NOT NULL DEFAULT '',
  value                TEXT NOT NULL DEFAULT '',
  value_num            REAL,
  at                   REAL
);
CREATE INDEX IF NOT EXISTS responses_attendee ON responses(attendee_id);
CREATE INDEX IF NOT EXISTS responses_session ON responses(session_id, kind);

-- a debrief sign-up or an offer hand-raise
CREATE TABLE IF NOT EXISTS signups (
  id         INTEGER PRIMARY KEY,
  person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  kind       TEXT NOT NULL,
  at         REAL,
  sent_at    REAL,
  UNIQUE(person_id, session_id, kind)
);
"""

# Responses are queued and written a second at a time. Two hundred people
# voting at once must not queue behind a disk, and losing the last second on a
# crash is tighter than the five seconds the room state already tolerates.
_QUEUE = queue.Queue()
_LOCK = threading.RLock()
_DB = None
_PATH = None
_WRITER = None
_IDLE = threading.Event()
_IDLE.set()
_KEY = None
# room_code -> open session id. Every audience action asks for this, so it must
# not be a query each time; close/discard drop the entry.
_SESSIONS = {}


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _anon_key(data_dir):
    """The key that turns a phone's token into an attendee hash. Kept on the
    volume: lose it and past evenings stop grouping, publish it and the hashes
    become reversible for anyone holding a list of tokens."""
    global _KEY
    if _KEY is not None:
        return _KEY
    path = os.path.join(data_dir, "anon.key")
    try:
        with open(path) as fh:
            _KEY = fh.read().strip()
    except OSError:
        _KEY = ""
    if not _KEY:
        _KEY = secrets.token_hex(32)
        try:
            with open(path, "w") as fh:
                fh.write(_KEY)
            os.chmod(path, 0o600)
        except OSError:
            pass
    return _KEY


def anon_id(session_id, pid):
    """Stable within one evening, meaningless outside it."""
    msg = "%s:%s" % (session_id, pid)
    return hmac.new(_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()[:24]


def init(data_dir):
    """Open (or create) the archive. Safe to call twice."""
    global _DB, _PATH, _WRITER
    with _LOCK:
        if _DB is not None:
            return _DB
        os.makedirs(data_dir, exist_ok=True)
        _anon_key(data_dir)
        _PATH = os.path.join(data_dir, "crm.sqlite3")
        _DB = sqlite3.connect(_PATH, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _migrate(_DB)
        _DB.executescript(SCHEMA)
        _DB.commit()
    if _WRITER is None:
        _WRITER = threading.Thread(target=_drain, name="crm-writer", daemon=True)
        _WRITER.start()
    return _DB


def path():
    return _PATH


def close():
    """Flush and shut down — used on SIGTERM so nothing queued is lost."""
    flush()
    with _LOCK:
        if _DB is not None:
            _DB.commit()


def _migrate(db):
    """Bring an archive written under the old, identified shape across: the
    person link and the check-in name come off the attendee rows, and the raw
    phone token is replaced by a per-session hash. One-way, on purpose."""
    pcols = [r[1] for r in db.execute("PRAGMA table_info(people)").fetchall()]
    if pcols and "occupation" not in pcols:
        db.execute("ALTER TABLE people ADD COLUMN occupation TEXT NOT NULL DEFAULT ''")
        db.commit()
    cols = [r[1] for r in db.execute("PRAGMA table_info(attendees)").fetchall()]
    if not cols or "anon" in cols:
        return
    print("  Archive: de-identifying the response history "
          "(names and person links come off; tokens become per-event hashes)")
    db.execute("ALTER TABLE attendees RENAME TO attendees_old")
    db.execute("""
      CREATE TABLE attendees (
        id            INTEGER PRIMARY KEY,
        session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        anon          TEXT NOT NULL,
        occupation    TEXT NOT NULL DEFAULT '',
        vibe          TEXT NOT NULL DEFAULT '',
        checked_in_at REAL,
        UNIQUE(session_id, anon))""")
    for row in db.execute("SELECT * FROM attendees_old").fetchall():
        db.execute("INSERT OR IGNORE INTO attendees (id, session_id, anon, occupation,"
                   " vibe, checked_in_at) VALUES (?,?,?,?,?,?)",
                   (row["id"], row["session_id"], anon_id(row["session_id"], row["pid"]),
                    row["occupation"], row["vibe"], row["checked_in_at"]))
    # carry the occupation people gave over to their contact record before the
    # link that made it findable disappears
    if "person_id" in cols:
        db.execute("""
          UPDATE people SET occupation = COALESCE((
            SELECT a.occupation FROM attendees_old a
             WHERE a.person_id = people.id AND a.occupation <> ''
             ORDER BY a.id DESC LIMIT 1), '')
          WHERE occupation = ''""")
    db.execute("DROP TABLE attendees_old")
    db.commit()


# ---------------------------------------------------------------------------
# the writer
# ---------------------------------------------------------------------------

def _drain():
    while True:
        item = _QUEUE.get()
        batch = [item]
        # take whatever else has piled up behind it
        try:
            while len(batch) < 500:
                batch.append(_QUEUE.get_nowait())
        except queue.Empty:
            pass
        try:
            with _LOCK:
                for fn, args in batch:
                    try:
                        fn(_DB, *args)
                    except Exception as exc:      # one bad row must not stop the rest
                        print("  (archive: %s)" % exc)
                _DB.commit()
        except Exception as exc:
            print("  (archive write failed: %s)" % exc)
        for _ in batch:
            _QUEUE.task_done()
        if _QUEUE.empty():
            _IDLE.set()
        else:
            time.sleep(0.05)


def _submit(fn, *args):
    if _DB is None:
        return
    _IDLE.clear()
    _QUEUE.put((fn, args))


def flush(timeout=5):
    """Block until everything queued has been written. For shutdown and tests."""
    if _DB is None:
        return
    deadline = time.time() + timeout
    while not _QUEUE.empty() and time.time() < deadline:
        time.sleep(0.01)
    _IDLE.wait(max(0.0, deadline - time.time()))


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def _open_session_id(db, room_code):
    row = db.execute(
        "SELECT id FROM sessions WHERE room_code=? AND closed_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (room_code,)).fetchone()
    return row["id"] if row else None


def session_for(room_code, event_id="", event_name=""):
    """The open session for this room, started if there isn't one. Called on the
    request thread because everything else needs the id, so the answer is
    cached — this runs on every vote."""
    cached = _SESSIONS.get(room_code)
    if cached is not None:
        return cached
    with _LOCK:
        if _DB is None:
            return None
        sid = _open_session_id(_DB, room_code)
        if sid is None:
            cur = _DB.execute(
                "INSERT INTO sessions (room_code, event_id, event_name, opened_at) "
                "VALUES (?,?,?,?)", (room_code, event_id or "", event_name or "", time.time()))
            _DB.commit()
            sid = cur.lastrowid
        _SESSIONS[room_code] = sid
        return sid


def close_session(room_code):
    """The room was switched off. The session stays open in the record until
    then, so an evening that pauses for a break is still one session."""
    with _LOCK:
        if _DB is None:
            return
        _DB.execute("UPDATE sessions SET closed_at=? WHERE room_code=? AND closed_at IS NULL",
                    (time.time(), room_code))
        _DB.commit()
    _SESSIONS.pop(room_code, None)


def discard_session(room_code):
    """A RESET means "that was a rehearsal". Nothing is deleted — it is marked,
    closed, and left out of the reports, so a wiped room never silently drops
    an evening that turned out to be real."""
    with _LOCK:
        if _DB is None:
            return
        _DB.execute("UPDATE sessions SET discarded=1, closed_at=COALESCE(closed_at,?) "
                    "WHERE room_code=? AND closed_at IS NULL", (time.time(), room_code))
        _DB.commit()
    _SESSIONS.pop(room_code, None)


# ---------------------------------------------------------------------------
# people and attendees
# ---------------------------------------------------------------------------

def _person_id(db, email, name="", occupation=""):
    """The contact record. Name and occupation are facts about a person they
    gave us directly — not answers, and never joined to any."""
    email = (email or "").strip()
    if not email:
        return None
    now = time.time()
    row = db.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()
    if row:
        db.execute("UPDATE people SET last_seen=?, name=COALESCE(NULLIF(?,''), name),"
                   " occupation=COALESCE(NULLIF(?,''), occupation) WHERE id=?",
                   (now, name or "", occupation or "", row["id"]))
        return row["id"]
    cur = db.execute("INSERT INTO people (email, name, occupation, first_seen, last_seen)"
                     " VALUES (?,?,?,?,?)", (email, name or "", occupation or "", now, now))
    return cur.lastrowid


def _attendee_id(db, session_id, pid, create=True):
    key = anon_id(session_id, pid)
    row = db.execute("SELECT id FROM attendees WHERE session_id=? AND anon=?",
                     (session_id, key)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    cur = db.execute("INSERT INTO attendees (session_id, anon, checked_in_at) VALUES (?,?,?)",
                     (session_id, key, time.time()))
    return cur.lastrowid


def _do_check_in(db, session_id, pid, occupation, vibe):
    aid = _attendee_id(db, session_id, pid)
    # the name they gave is deliberately not written here: this half of the
    # archive holds what a room thought, not who was in it
    db.execute("UPDATE attendees SET occupation=?, vibe=? WHERE id=?",
               (occupation or "", vibe or "", aid))


def check_in(session_id, pid, occupation, vibe):
    if session_id:
        _submit(_do_check_in, session_id, pid, occupation, vibe)


def _do_record(db, session_id, pid, kind, topic_index, topic_q, inter_q, value, value_num, at):
    aid = _attendee_id(db, session_id, pid)
    db.execute(
        "INSERT INTO responses (attendee_id, session_id, kind, topic_index, topic_question,"
        " interaction_question, value, value_num, at) VALUES (?,?,?,?,?,?,?,?,?)",
        (aid, session_id, kind, topic_index, topic_q or "", inter_q or "",
         "" if value is None else str(value)[:500], value_num, at))


def record(session_id, pid, kind, topic_index=None, topic_question="",
           interaction_question="", value="", value_num=None):
    """One thing one person did. Queued, never written on the request thread."""
    if session_id and pid:
        _submit(_do_record, session_id, pid, kind, topic_index, topic_question,
                interaction_question, value, value_num, time.time())


def _do_contact(db, email, name, occupation):
    _person_id(db, email, name, occupation)


def contact(email, name="", occupation=""):
    """Record who someone is. Nothing about their answers travels with this."""
    if email:
        _submit(_do_contact, email, name, occupation)


def _do_signup(db, session_id, email, name, occupation, kind):
    person = _person_id(db, email, name, occupation)
    if person is None:
        return
    # which evenings someone came to, and whether they put a hand up — the
    # engagement record, kept without reaching into what they said
    db.execute("INSERT OR IGNORE INTO signups (person_id, session_id, kind, at) "
               "VALUES (?,?,?,?)", (person, session_id, kind, time.time()))


def signup(session_id, email, name="", occupation="", kind="debrief"):
    if email:
        _submit(_do_signup, session_id, email, name, occupation, kind)


def mark_sent(email):
    with _LOCK:
        if _DB is None:
            return
        _DB.execute("UPDATE signups SET sent_at=? WHERE person_id="
                    "(SELECT id FROM people WHERE email=?) AND kind='debrief'",
                    (time.time(), email))
        _DB.commit()


def set_suppressed(email, on=True):
    with _LOCK:
        if _DB is None:
            return
        _DB.execute("UPDATE people SET suppressed=? WHERE email=?", (1 if on else 0, email))
        _DB.commit()


# ---------------------------------------------------------------------------
# deletion — the other half of keeping records
# ---------------------------------------------------------------------------

def forget_pid(session_id, pid):
    """One phone asking to be forgotten, during the event it is standing in.
    Only possible here: the phone still knows its own token, so the hash can be
    recomputed. After the evening nobody can find these rows again, which is
    what makes them anonymous."""
    flush()
    with _LOCK:
        if _DB is None:
            return 0
        cur = _DB.execute("DELETE FROM attendees WHERE session_id=? AND anon=?",
                          (session_id, anon_id(session_id, pid)))
        _DB.commit()
        return cur.rowcount


def forget_person(email):
    """The contact record and the record of which evenings they came to. Their
    answers are not touched because they cannot be found — there is no path from
    an address to a response, by design."""
    flush()
    with _LOCK:
        if _DB is None:
            return 0
        row = _DB.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()
        if not row:
            return 0
        _DB.execute("DELETE FROM signups WHERE person_id=?", (row["id"],))
        _DB.execute("DELETE FROM people WHERE id=?", (row["id"],))
        _DB.commit()
        return 1


def purge_older_than(days):
    """Retention. Off by default — the point of this archive is that it keeps —
    but a number here is easier to defend than "forever" if anyone ever asks."""
    if not days:
        return 0
    cutoff = time.time() - days * 86400
    flush()
    with _LOCK:
        if _DB is None:
            return 0
        rows = _DB.execute("SELECT id FROM sessions WHERE COALESCE(closed_at, opened_at) < ?",
                           (cutoff,)).fetchall()
        for s in rows:
            _DB.execute("DELETE FROM responses WHERE session_id=?", (s["id"],))
            _DB.execute("DELETE FROM attendees WHERE session_id=?", (s["id"],))
            _DB.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
        _DB.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def query(sql, args=()):
    with _LOCK:
        if _DB is None:
            return []
        return [dict(r) for r in _DB.execute(sql, args).fetchall()]


def summary():
    """Enough to prove on sight that the archive is doing its job."""
    with _LOCK:
        if _DB is None:
            return {}
        one = lambda sql: _DB.execute(sql).fetchone()[0]
        return {
            "sessions": one("SELECT COUNT(*) FROM sessions WHERE discarded=0"),
            "rehearsals": one("SELECT COUNT(*) FROM sessions WHERE discarded=1"),
            "attendees": one("SELECT COUNT(*) FROM attendees"),
            "contacts": one("SELECT COUNT(*) FROM signups"),
            "people": one("SELECT COUNT(*) FROM people"),
            "responses": one("SELECT COUNT(*) FROM responses"),
        }


# ---------------------------------------------------------------------------
# bringing the old files in
# ---------------------------------------------------------------------------

def import_legacy(leads_dir, interest_dir):
    """Fold the JSON files that came before into the archive, once. Idempotent:
    people are keyed by address and sign-ups have a unique constraint, so
    running it again changes nothing."""
    if _DB is None:
        return {"people": 0, "signups": 0}
    added_people = added_signups = 0
    for folder, kind in ((leads_dir, "debrief"), (interest_dir, "offer")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".json"):
                continue
            room = fn[:-5]
            try:
                with open(os.path.join(folder, fn)) as fh:
                    rows = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(rows, list):
                continue
            with _LOCK:
                # these came from evenings that are already over
                s = _DB.execute("SELECT id FROM sessions WHERE room_code=? ORDER BY id LIMIT 1",
                                (room,)).fetchone()
                if s:
                    sid = s["id"]
                else:
                    name = next((r.get("eventName", "") for r in rows if r.get("eventName")), "")
                    when = min([r.get("at", time.time()) for r in rows] or [time.time()])
                    sid = _DB.execute(
                        "INSERT INTO sessions (room_code, event_id, event_name, opened_at,"
                        " closed_at) VALUES (?,?,?,?,?)",
                        (room, "", name, when, when)).lastrowid
                for r in rows:
                    email = (r.get("email") or "").strip()
                    if not email:
                        continue
                    before = _DB.execute("SELECT COUNT(*) FROM people").fetchone()[0]
                    person = _person_id(_DB, email, r.get("name", ""))
                    if _DB.execute("SELECT COUNT(*) FROM people").fetchone()[0] > before:
                        added_people += 1
                    cur = _DB.execute(
                        "INSERT OR IGNORE INTO signups (person_id, session_id, kind, at, sent_at)"
                        " VALUES (?,?,?,?,?)",
                        (person, sid, kind, r.get("at"), r.get("sentAt")))
                    added_signups += cur.rowcount
                _DB.commit()
    return {"people": added_people, "signups": added_signups}


# ---------------------------------------------------------------------------
# the views behind /crm
# ---------------------------------------------------------------------------

# Everything below reads. Rehearsal sessions (a room someone RESET) are left
# out throughout — they are kept so nothing is ever silently dropped, not so
# they can skew a count.

# Everything here comes from the contact record and the sign-up log. There is
# no column that could reach a response, because there is no join that reaches
# one — which is the whole point of the shape.
_PERSON_COLUMNS = """
  p.id, p.email, p.name, p.occupation, p.first_seen, p.last_seen, p.suppressed,
  (SELECT COUNT(DISTINCT g.session_id) FROM signups g
     JOIN sessions s ON s.id = g.session_id AND s.discarded = 0
    WHERE g.person_id = p.id) AS nights,
  (SELECT COUNT(*) FROM signups g
    WHERE g.person_id = p.id AND g.kind = 'offer') AS hands
"""

_SORTS = {
    "recent": "p.last_seen DESC",
    "nights": "nights DESC, p.last_seen DESC",
    "hands": "hands DESC, nights DESC",
    "occupation": "p.occupation, p.name COLLATE NOCASE",
    "name": "p.name COLLATE NOCASE, p.email",
}


def people(search="", sort="recent", limit=200, offset=0):
    like = "%" + (search or "").strip() + "%"
    order = _SORTS.get(sort, _SORTS["recent"])
    return query(
        "SELECT %s FROM people p WHERE (?='' OR p.email LIKE ? OR p.name LIKE ?) "
        "ORDER BY %s LIMIT ? OFFSET ?" % (_PERSON_COLUMNS, order),
        ((search or "").strip(), like, like, limit, offset))


def people_count(search=""):
    like = "%" + (search or "").strip() + "%"
    rows = query("SELECT COUNT(*) n FROM people p WHERE (?='' OR p.email LIKE ? OR p.name LIKE ?)",
                 ((search or "").strip(), like, like))
    return rows[0]["n"] if rows else 0


def person(person_id):
    """One contact: who they are and which evenings they came to. What they
    said that night is not here, and cannot be — nothing joins an address to an
    answer."""
    rows = query("SELECT %s FROM people p WHERE p.id = ?" % _PERSON_COLUMNS, (person_id,))
    if not rows:
        return None
    out = rows[0]
    out["signups"] = query("""
        SELECT g.kind, g.at, g.sent_at, s.room_code, s.event_name, s.opened_at
        FROM signups g LEFT JOIN sessions s ON s.id = g.session_id
        WHERE g.person_id = ? ORDER BY g.at DESC""", (person_id,))
    return out


def sessions(limit=100):
    return query("""
        SELECT s.id, s.room_code, s.event_name, s.opened_at, s.closed_at, s.discarded,
               (SELECT COUNT(*) FROM attendees a WHERE a.session_id = s.id) AS attendees,
               (SELECT COUNT(*) FROM signups g WHERE g.session_id = s.id) AS contacts,
               (SELECT COUNT(*) FROM responses r WHERE r.session_id = s.id) AS responses
        FROM sessions s ORDER BY s.opened_at DESC LIMIT ?""", (limit,))


def session_report(session_id):
    """One evening, in the shape you would show a venue or a sponsor."""
    rows = query("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return None
    out = rows[0]
    out["attendees"] = query(
        "SELECT COUNT(*) n FROM attendees WHERE session_id = ?", (session_id,))[0]["n"]
    out["contacts"] = query(
        "SELECT COUNT(*) n FROM signups WHERE session_id = ?", (session_id,))[0]["n"]
    out["occupations"] = query("""
        SELECT occupation AS label, COUNT(*) AS count FROM attendees
        WHERE session_id = ? AND occupation <> ''
        GROUP BY occupation ORDER BY count DESC, label""", (session_id,))
    out["vibes"] = query("""
        SELECT vibe AS id, COUNT(*) AS count FROM attendees
        WHERE session_id = ? AND vibe <> '' GROUP BY vibe ORDER BY count DESC""",
        (session_id,))
    out["activity"] = query("""
        SELECT kind, COUNT(*) AS count FROM responses
        WHERE session_id = ? GROUP BY kind ORDER BY count DESC""", (session_id,))
    # How the room split on each topic. Only each person's *last* word counts:
    # every position they held is kept — watching minds change is the point of
    # the thing — but a report that counted them all would count a person who
    # switched sides twice, and add up to more people than were in the room.
    out["topics"] = query("""
        SELECT topic_index, topic_question,
               SUM(value = 'agree')    AS agree,
               SUM(value = 'disagree') AS disagree,
               SUM(value = 'unsure')   AS unsure
        FROM responses r
        WHERE r.session_id = ? AND r.kind = 'sentiment'
          AND r.id = (SELECT r2.id FROM responses r2
                       WHERE r2.attendee_id = r.attendee_id AND r2.kind = 'sentiment'
                         AND r2.topic_index = r.topic_index
                       ORDER BY r2.at DESC, r2.id DESC LIMIT 1)
        GROUP BY topic_index ORDER BY topic_index""", (session_id,))
    out["vibe_shift"] = query("""
        SELECT a.vibe, r.value AS answer, COUNT(*) AS count
        FROM responses r JOIN attendees a ON a.id = r.attendee_id
        WHERE r.session_id = ? AND r.kind = 'sentiment' AND a.vibe <> ''
        GROUP BY a.vibe, r.value ORDER BY count DESC""", (session_id,))
    out["questions"] = query(
        "SELECT value AS text FROM responses "
        "WHERE session_id = ? AND kind = 'question' ORDER BY at", (session_id,))
    out["answers"] = answers_by_occupation(session_id)
    return out


# Groups smaller than this are folded into "Other" in a cross-tab. In a room of
# forty, a breakdown showing one Retired person who disagreed is a name to
# anyone who was there — the counts are anonymous, the arithmetic needn't be.
MIN_GROUP = 3


def _latest_only(kind):
    """Every position a person held is kept; a report takes their last word.
    Otherwise someone who changed their mind twice is three people, and a room
    of forty reports as sixty."""
    return ("""
        AND r.id = (SELECT r2.id FROM responses r2
                     WHERE r2.attendee_id = r.attendee_id AND r2.kind = '%s'
                       AND r2.interaction_question = r.interaction_question
                       AND r2.topic_index = r.topic_index
                     ORDER BY r2.at DESC, r2.id DESC LIMIT 1)""" % kind)


def answers_by_occupation(session_id, kinds=("sentiment", "poll", "slider")):
    """What the room thought, and who thought it — the whole reason the
    response half carries occupation and vibe.

    One block per question: the overall split as counts and percentages, then
    the same split for each occupation big enough to report."""
    blocks = []
    for kind in kinds:
        questions = query("""
            SELECT DISTINCT topic_index, topic_question, interaction_question
            FROM responses WHERE session_id = ? AND kind = ?
            ORDER BY topic_index""", (session_id, kind))
        for q in questions:
            rows = query("""
                SELECT r.value AS answer, a.occupation, COUNT(*) AS count
                FROM responses r JOIN attendees a ON a.id = r.attendee_id
                WHERE r.session_id = ? AND r.kind = ? AND r.topic_index IS ?
                  AND r.interaction_question = ? %s
                GROUP BY r.value, a.occupation""" % _latest_only(kind),
                (session_id, kind, q["topic_index"], q["interaction_question"]))
            if not rows:
                continue
            total = sum(r["count"] for r in rows)
            overall = {}
            per_job = {}
            for r in rows:
                overall[r["answer"]] = overall.get(r["answer"], 0) + r["count"]
                job = r["occupation"] or "Not given"
                per_job.setdefault(job, {})[r["answer"]] = r["count"]

            answers = sorted(overall, key=lambda a: -overall[a])
            groups, small = [], {}
            for job, counts in per_job.items():
                n = sum(counts.values())
                if n < MIN_GROUP:
                    for a, c in counts.items():
                        small[a] = small.get(a, 0) + c
                else:
                    groups.append({"label": job, "total": n,
                                   "counts": [counts.get(a, 0) for a in answers],
                                   "pcts": [round(100 * counts.get(a, 0) / n) for a in answers]})
            groups.sort(key=lambda g: -g["total"])
            if small:
                n = sum(small.values())
                groups.append({"label": "Other (groups too small to report)", "total": n,
                               "counts": [small.get(a, 0) for a in answers],
                               "pcts": [round(100 * small.get(a, 0) / n) for a in answers],
                               "small": True})
            blocks.append({
                "kind": kind,
                "question": q["interaction_question"] or q["topic_question"],
                "topic": q["topic_question"],
                "answers": answers,
                "total": total,
                "counts": [overall[a] for a in answers],
                "pcts": [round(100 * overall[a] / total) for a in answers],
                "groups": groups,
            })
    return blocks


def export_rows(what, session_id=None):
    """(header, rows) for a CSV. Deliberately flat — this is the format that
    imports into anything else you might move to later."""
    if what == "people":
        rows = query("SELECT %s FROM people p ORDER BY p.last_seen DESC" % _PERSON_COLUMNS)
        header = ["email", "name", "occupation", "events_attended", "hands_raised",
                  "first_seen", "last_seen", "unsubscribed"]
        return header, [[r["email"], r["name"], r["occupation"] or "", r["nights"],
                         r["hands"], _stamp(r["first_seen"]), _stamp(r["last_seen"]),
                         "yes" if r["suppressed"] else ""] for r in rows]
    if what == "responses":
        where = "WHERE s.discarded = 0"
        args = ()
        if session_id:
            where += " AND s.id = ?"
            args = (session_id,)
        # no name, no email, and the attendee key is per-event: this file is
        # safe to hand to an analyst, a venue or a sponsor
        rows = query("""
            SELECT s.room_code, s.event_name, a.anon, a.occupation, a.vibe,
                   r.kind, r.topic_question, r.interaction_question, r.value, r.at
            FROM responses r
            JOIN attendees a ON a.id = r.attendee_id
            JOIN sessions s ON s.id = r.session_id
            %s ORDER BY r.at""" % where, args)
        header = ["room", "event", "attendee", "occupation", "vibe", "kind",
                  "topic", "question", "answer", "when"]
        return header, [[r["room_code"], r["event_name"], r["anon"][:8],
                         r["occupation"], r["vibe"], r["kind"], r["topic_question"],
                         r["interaction_question"], r["value"], _stamp(r["at"])]
                        for r in rows]
    if what == "attendance":
        # who came to what, from the sign-up log — the contact half
        rows = query("""
            SELECT s.room_code, s.event_name, s.opened_at, p.email, p.name, p.occupation,
                   g.kind, g.at
            FROM signups g JOIN people p ON p.id = g.person_id
            JOIN sessions s ON s.id = g.session_id
            WHERE s.discarded = 0 ORDER BY s.opened_at DESC, p.name""")
        header = ["room", "event", "when", "email", "name", "occupation", "signed_up_for"]
        return header, [[r["room_code"], r["event_name"], _stamp(r["opened_at"]),
                         r["email"], r["name"], r["occupation"], r["kind"]]
                        for r in rows]
    return [], []


def _stamp(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
