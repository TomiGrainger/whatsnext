"""The archive: every event, everyone who came, and everything they did.

The live room stays in memory — a vote must never wait on a disk. This module
takes a copy of what happened and writes it down, so that RESET clears the room
without clearing the record, and so questions that span events ("founders who
have been twice and raised a hand") become one query instead of a script.

SQLite via the standard library, so the zero-dependency rule still holds. One
file on the data volume.

The shape worth understanding is that `attendees` sits between a phone and a
person. Responses hang off the attendee, never off the person, so:

  · everyone's answers are recorded, identified or not;
  · identity is layered on afterwards for those who give an email, without
    assuming anything about those who don't;
  · and deleting a person is one join away from deleting everything they did,
    which is what makes the privacy promise keepable rather than aspirational.
"""

import json
import os
import queue
import sqlite3
import threading
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS people (
  id          INTEGER PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name        TEXT NOT NULL DEFAULT '',
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

-- one phone in one session
CREATE TABLE IF NOT EXISTS attendees (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  pid           TEXT NOT NULL,
  person_id     INTEGER REFERENCES people(id) ON DELETE SET NULL,
  name          TEXT NOT NULL DEFAULT '',
  occupation    TEXT NOT NULL DEFAULT '',
  vibe          TEXT NOT NULL DEFAULT '',
  checked_in_at REAL,
  UNIQUE(session_id, pid)
);
CREATE INDEX IF NOT EXISTS attendees_person ON attendees(person_id);

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
# room_code -> open session id. Every audience action asks for this, so it must
# not be a query each time; close/discard drop the entry.
_SESSIONS = {}


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def init(data_dir):
    """Open (or create) the archive. Safe to call twice."""
    global _DB, _PATH, _WRITER
    with _LOCK:
        if _DB is not None:
            return _DB
        os.makedirs(data_dir, exist_ok=True)
        _PATH = os.path.join(data_dir, "crm.sqlite3")
        _DB = sqlite3.connect(_PATH, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
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

def _person_id(db, email, name=""):
    email = (email or "").strip()
    if not email:
        return None
    now = time.time()
    row = db.execute("SELECT id, name FROM people WHERE email=?", (email,)).fetchone()
    if row:
        db.execute("UPDATE people SET last_seen=?, name=COALESCE(NULLIF(?,''), name) "
                   "WHERE id=?", (now, name or "", row["id"]))
        return row["id"]
    cur = db.execute("INSERT INTO people (email, name, first_seen, last_seen) VALUES (?,?,?,?)",
                     (email, name or "", now, now))
    return cur.lastrowid


def _attendee_id(db, session_id, pid, create=True):
    row = db.execute("SELECT id FROM attendees WHERE session_id=? AND pid=?",
                     (session_id, pid)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    cur = db.execute("INSERT INTO attendees (session_id, pid, checked_in_at) VALUES (?,?,?)",
                     (session_id, pid, time.time()))
    return cur.lastrowid


def _do_check_in(db, session_id, pid, name, occupation, vibe):
    aid = _attendee_id(db, session_id, pid)
    db.execute("UPDATE attendees SET name=?, occupation=?, vibe=? WHERE id=?",
               (name or "", occupation or "", vibe or "", aid))


def check_in(session_id, pid, name, occupation, vibe):
    if session_id:
        _submit(_do_check_in, session_id, pid, name, occupation, vibe)


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


def _do_identify(db, session_id, pid, email, name):
    person = _person_id(db, email, name)
    if person is None:
        return
    if session_id and pid:
        aid = _attendee_id(db, session_id, pid)
        db.execute("UPDATE attendees SET person_id=? WHERE id=?", (person, aid))


def identify(session_id, pid, email, name=""):
    """Attach a phone to a person. This is the join the app didn't have: it
    retroactively claims everything that phone already did tonight."""
    if email:
        _submit(_do_identify, session_id, pid, email, name)


def _do_signup(db, session_id, pid, email, name, kind):
    person = _person_id(db, email, name)
    if person is None:
        return
    if pid and session_id:
        aid = _attendee_id(db, session_id, pid)
        db.execute("UPDATE attendees SET person_id=? WHERE id=?", (person, aid))
    db.execute("INSERT OR IGNORE INTO signups (person_id, session_id, kind, at) "
               "VALUES (?,?,?,?)", (person, session_id, kind, time.time()))


def signup(session_id, pid, email, name="", kind="debrief"):
    if email:
        _submit(_do_signup, session_id, pid, email, name, kind)


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
    """One phone asking to be forgotten, here and now. Cascades to responses."""
    flush()
    with _LOCK:
        if _DB is None:
            return 0
        cur = _DB.execute("DELETE FROM attendees WHERE session_id=? AND pid=?",
                          (session_id, pid))
        _DB.commit()
        return cur.rowcount


def forget_person(email):
    """Everything, everywhere, for one address. Attendee rows and their
    responses go with the person, across every event they ever came to."""
    flush()
    with _LOCK:
        if _DB is None:
            return 0
        row = _DB.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()
        if not row:
            return 0
        pid_rows = _DB.execute("SELECT id FROM attendees WHERE person_id=?",
                               (row["id"],)).fetchall()
        for a in pid_rows:
            _DB.execute("DELETE FROM responses WHERE attendee_id=?", (a["id"],))
        _DB.execute("DELETE FROM attendees WHERE person_id=?", (row["id"],))
        _DB.execute("DELETE FROM signups WHERE person_id=?", (row["id"],))
        _DB.execute("DELETE FROM people WHERE id=?", (row["id"],))
        _DB.commit()
        return len(pid_rows) + 1


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
            "identified": one("SELECT COUNT(*) FROM attendees WHERE person_id IS NOT NULL"),
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
                    if r.get("pid"):
                        aid = _attendee_id(_DB, sid, r["pid"])
                        _DB.execute("UPDATE attendees SET person_id=?, name=COALESCE(NULLIF(?,''),name)"
                                    " WHERE id=?", (person, r.get("name", ""), aid))
                    cur = _DB.execute(
                        "INSERT OR IGNORE INTO signups (person_id, session_id, kind, at, sent_at)"
                        " VALUES (?,?,?,?,?)",
                        (person, sid, kind, r.get("at"), r.get("sentAt")))
                    added_signups += cur.rowcount
                _DB.commit()
    return {"people": added_people, "signups": added_signups}
