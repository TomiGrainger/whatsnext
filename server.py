#!/usr/bin/env python3
"""
THE UPGRADE LIVE — real-time audience engagement server.

Pure Python standard library. No dependencies, no build step.
    python3 server.py            # serves on http://localhost:8000

Surfaces (each takes an optional ?room=CODE, default WN25):
    /              audience (mobile)   -> designs/audience-01..08
    /moderator     control room        -> designs/moderator-dashboard
    /projector     room display        -> designs/projector-display

Features
    • Config-driven — event content (topics, polls, word clouds, sliders, emoji,
                     branding) lives in events/demo_event.json, not in this file.
                     A topic's own `type`/`interaction` decides what screen the
                     audience/moderator/projector render for it.
    • Multi-room   — independent rooms keyed by code, opened deliberately from
                     /setup. An unopened code renders a "room not open" screen
                     rather than springing a room into being. WN25 is the demo
                     room and the only one seeded with the mockup's numbers.
    • Persistence  — every room is saved to rooms_state.json and reloaded on start.
    • Real-time    — Server-Sent Events (/events) push each room's snapshot to its
                     surfaces; clients send actions via POST /api/action.
"""

import copy
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qr

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
EVENTS_DIR = os.path.join(ROOT, "events")
DEMO_EVENT_FILE = os.path.join(EVENTS_DIR, "demo_event.json")
STATE_FILE = os.path.join(ROOT, "rooms_state.json")
LEADS_DIR = os.path.join(ROOT, "leads")
DEFAULT_ROOM = "WN25"

LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Passcode. One shared code guards the moderator and setup surfaces and every
# write; the audience surface stays open so guests just scan and join. Set
# PASSCODE to choose it, otherwise one is generated and printed at startup.
# Sessions are opaque tokens held in memory — restarting the server logs the
# crew out, which is fine for a single-evening tool.
# ---------------------------------------------------------------------------

PASSCODE = os.environ.get("PASSCODE") or "".join(secrets.choice("0123456789") for _ in range(6))
SESSIONS = set()
SESSION_LOCK = threading.Lock()
COOKIE = "upgrade_crew"


def new_session():
    token = secrets.token_urlsafe(24)
    with SESSION_LOCK:
        SESSIONS.add(token)
    return token


def valid_session(token):
    if not token:
        return False
    with SESSION_LOCK:
        return token in SESSIONS


DEFAULT_EVENT_ID = "demo_event"

# id -> config. An event config is pure content: brand, topics, and (per
# ask_audience topic) the poll/wordcloud/slider/emoji/ranking definition that
# goes with it. No runtime counters live here — see _init_topic_runtime.
EVENTS = {}


def upgrade_config(cfg):
    """Events written before interactions were nested used a flat list where each
    topic was either a discussion or a single interaction. Fold that shape into
    the current one so older files keep working: an interaction attaches to the
    discussion it followed, or becomes its own topic if it came first."""
    topics = cfg.get("topics", [])
    if not any("type" in t for t in topics):
        return cfg

    upgraded = []
    for t in topics:
        if t.get("type") == "discussion":
            upgraded.append({
                "id": t.get("id", "t%d" % (len(upgraded) + 1)),
                "question": t.get("question", ""),
                "settings": {"whatsNextThreshold": t.get("settings", {}).get("whatsNextThreshold", 10)},
                "seed": t.get("seed", {}),
                "interactions": [],
            })
            continue
        item = {k: v for k, v in t.items() if k not in ("type", "interaction")}
        item["kind"] = t.get("interaction")
        item["settings"] = {"duration": t.get("settings", {}).get("duration", 60)}
        if not upgraded:
            upgraded.append({"id": "t%d" % (len(upgraded) + 1), "question": t.get("question", ""),
                             "settings": {"whatsNextThreshold": 10}, "interactions": []})
        upgraded[-1]["interactions"].append(item)

    cfg = dict(cfg)
    cfg["topics"] = upgraded
    return cfg


def load_events():
    EVENTS.clear()
    for name in sorted(os.listdir(EVENTS_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(EVENTS_DIR, name)) as fh:
                EVENTS[name[:-5]] = upgrade_config(json.load(fh))
        except Exception as exc:
            print("  (skipping events/%s: %s)" % (name, exc))
    return EVENTS


def get_event(event_id):
    return EVENTS.get(event_id) or EVENTS.get(DEFAULT_EVENT_ID)


def event_summaries():
    out = []
    for eid, cfg in sorted(EVENTS.items()):
        kinds = []
        for t in cfg["topics"]:
            for item in t.get("interactions", []):
                kinds.append(item["kind"])
        out.append({
            "id": eid,
            "brand": cfg.get("brand", ""),
            "eventName": cfg.get("eventName", ""),
            "topicCount": len(cfg["topics"]),
            "interactionCount": len(kinds),
            "kinds": kinds,
        })
    return out


# ---------------------------------------------------------------------------
# Sign-ups. Guests can leave an email on the closing screen to get the debrief;
# they land in leads/<ROOM>.json next to the event that collected them.
# ---------------------------------------------------------------------------

LEAD_LOCK = threading.Lock()


def _leads_path(code):
    return os.path.join(LEADS_DIR, sanitize_code(code) + ".json")


def read_leads(code):
    try:
        with open(_leads_path(code)) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def plausible_email(value):
    """Deliberately loose — the point is to catch typos and junk, not to police
    what a valid address looks like."""
    value = (value or "").strip()
    if not 5 <= len(value) <= 200 or value.count("@") != 1:
        return None
    local, _, domain = value.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    if any(ch.isspace() for ch in value):
        return None
    return value


def add_lead(code, email, name):
    """Returns True if stored, False if this address already signed up."""
    code = sanitize_code(code)
    with LEAD_LOCK:
        leads = read_leads(code)
        if any(l.get("email", "").lower() == email.lower() for l in leads):
            return False
        room = ROOMS.get(code) or {}
        leads.append({
            "email": email,
            "name": name or "",
            "at": time.time(),
            "room": code,
            "eventId": room.get("eventId", ""),
            "eventName": room.get("eventName", ""),
        })
        try:
            os.makedirs(LEADS_DIR, exist_ok=True)
            tmp = _leads_path(code) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(leads, fh, indent=2)
            os.replace(tmp, _leads_path(code))
        except OSError as exc:
            print("  (could not save sign-up: %s)" % exc)
            return False
    return True


def html_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def lan_host():
    """The address phones on the same Wi-Fi should use. Opening a UDP socket
    to a public address asks the OS which local interface it would route
    through — nothing is actually sent."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "localhost"


def join_url(code, host=None):
    return "http://%s:%d/?room=%s" % (host or lan_host(), PORT, sanitize_code(code))


def _cid():
    return uuid.uuid4().hex[:8]


def sanitize_code(code):
    code = "".join(ch for ch in (code or "") if ch.isalnum()).upper()[:8]
    return code or DEFAULT_ROOM


def _seed_challenges(config):
    out = []
    for c in config.get("seedChallenges", []):
        out.append({
            "id": _cid(), "name": c["name"], "initials": _initials(c["name"]),
            "text": c["text"], "at": time.time() - c.get("agoSeconds", 60),
        })
    return out


# Interactions whose numbers are held back until the moderator reveals them.
# A word cloud and emoji shower are the opposite — watching them build is the
# whole point — and a ranking reads as a live leaderboard, so those stay open.
HIDEABLE = ("poll", "slider")


def _init_interaction_runtime(item, seed):
    """Runtime tallies for one interaction, shaped by its kind — never by its
    content. `seed` (demo-only) comes from the interaction's own `seed` block."""
    seed_data = item.get("seed", {}) if seed else {}
    kind = item["kind"]
    if kind == "poll":
        seed_votes = seed_data.get("votes", {})
        return {"votes": {o["id"]: seed_votes.get(o["id"], 0) for o in item["options"]}, "_votes": {}}
    if kind == "wordcloud":
        return {"words": dict(seed_data.get("words", {})), "_votes": {}}
    if kind == "emoji":
        seed_counts = seed_data.get("counts", {})
        return {"counts": {o["id"]: seed_counts.get(o["id"], 0) for o in item["options"]}, "_votes": {}}
    if kind == "slider":
        return {"sum": seed_data.get("sum", 0), "count": seed_data.get("count", 0), "_votes": {}}
    if kind == "ranking":
        seed_scores = seed_data.get("scores", {})
        return {
            "scores": {i["id"]: seed_scores.get(i["id"], 0) for i in item["items"]},
            "submissions": seed_data.get("submissions", 0), "_votes": {},
        }
    raise ValueError("unknown interaction kind: %r" % kind)


def _init_topic_runtime(topic, seed):
    """A topic's runtime: its always-present discussion tallies, plus one runtime
    per configured interaction."""
    seed_data = topic.get("seed", {}) if seed else {}
    return {
        "sentiment": dict(seed_data.get("sentiment", {"agree": 0, "disagree": 0, "unsure": 0})),
        "sentimentHistory": list(seed_data.get("sentimentHistory", [])),
        "whatsNext": {
            "votes": seed_data.get("whatsNextVotes", 0),
            "threshold": topic.get("settings", {}).get("whatsNextThreshold", 10),
        },
        "responses": seed_data.get("responses", 0),
        "_votes": {"sentiment": {}, "next": []},
        "interactions": [_init_interaction_runtime(i, seed) for i in topic.get("interactions", [])],
    }


def _activate_topic(r, index):
    """Move to a topic. A topic always opens on its discussion, which is untimed —
    it stays up until the moderator launches something or moves on."""
    r["topicIndex"] = index
    r["activeInteraction"] = None
    r["mode"] = "discussion"
    r["paused"] = False
    r["timeRemaining"] = 0
    r["revealed"] = True


def _activate_interaction(r, index):
    topic = r["topics"][r["topicIndex"]]
    items = topic.get("interactions", [])
    if not 0 <= index < len(items):
        return
    r["activeInteraction"] = index
    r["mode"] = items[index]["kind"]
    r["paused"] = False
    r["timeRemaining"] = items[index].get("settings", {}).get("duration", 60)
    # a poll or slider opens closed — the room votes blind until the reveal
    r["revealed"] = items[index]["kind"] not in HIDEABLE


def new_room(code, event_id=DEFAULT_EVENT_ID, seed=False):
    """A room pairs one event's config (content) with fresh runtime state (votes,
    tallies, timers). The config is copied in, so editing the event file later
    never mutates a room that is already live. When seed=True the runtime is
    pre-populated from each topic's `seed` block (the WN25 demo room); otherwise
    everything starts zeroed — same event, a fresh run of it."""
    config = get_event(event_id)
    topics = copy.deepcopy(config["topics"])
    room = {
        "code": code,
        "eventId": event_id,
        "brand": config.get("brand", "LIVE EVENT"),
        "eventName": config.get("eventName", "EVENT"),
        "topics": topics,
        "topicIndex": 0,
        "topicCount": len(topics),
        "topicRuntime": [_init_topic_runtime(t, seed) for t in topics],
        "activeInteraction": None,
        "mode": "discussion",
        "paused": False,
        "timeRemaining": 0,
        "closed": False,
        "challenges": _seed_challenges(config) if seed else [],
        "invited": [],
        "simParticipants": config.get("seedSimParticipants", 0) if seed else 0,
    }
    _activate_topic(room, 0)
    return room


# ---------------------------------------------------------------------------
# Event authoring (POST /api/events) — everything here treats the payload as
# untrusted: types and lengths are checked, counts are capped, ids are
# generated rather than accepted, and the filename is a sanitized slug that
# cannot escape EVENTS_DIR.
# ---------------------------------------------------------------------------

INTERACTIONS = ("poll", "wordcloud", "emoji", "slider", "ranking")
MAX_TOPICS = 40
MAX_INTERACTIONS = 12
MAX_OPTIONS = 10


class Invalid(Exception):
    pass


def _text(value, field, max_len, required=True):
    if not isinstance(value, str):
        if required:
            raise Invalid("%s is required" % field)
        return ""
    value = value.strip()
    if required and not value:
        raise Invalid("%s is required" % field)
    if len(value) > max_len:
        raise Invalid("%s must be %d characters or fewer" % (field, max_len))
    return value


def _int(value, field, low, high, default):
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise Invalid("%s must be a number" % field)
    if not low <= n <= high:
        raise Invalid("%s must be between %d and %d" % (field, low, high))
    return n


def _labelled_list(raw, field, key, max_len, min_count):
    if not isinstance(raw, list):
        raise Invalid("%s is required" % field)
    items = []
    for i, entry in enumerate(raw):
        if isinstance(entry, dict):
            entry = entry.get(key)
        text = _text(entry, "%s #%d" % (field, i + 1), max_len, required=False)
        if text:
            items.append({"id": "o%d" % (len(items) + 1), key: text})
    if len(items) < min_count:
        raise Invalid("%s needs at least %d entries" % (field, min_count))
    if len(items) > MAX_OPTIONS:
        raise Invalid("%s allows at most %d entries" % (field, MAX_OPTIONS))
    return items


def _validate_interaction(raw, topic_no, index):
    """One interaction hanging off a topic. Unlike the topic's discussion, an
    interaction is timed — it counts down from its own duration."""
    if not isinstance(raw, dict):
        raise Invalid("Topic %d, item %d is malformed" % (topic_no, index + 1))
    kind = raw.get("kind")
    if kind not in INTERACTIONS:
        raise Invalid("Topic %d, item %d has an unknown type" % (topic_no, index + 1))

    label = "Topic %d %s" % (topic_no, kind)
    out = {
        "id": "i%d" % (index + 1),
        "kind": kind,
        "question": _text(raw.get("question"), "%s question" % label, 200),
        "settings": {"duration": _int(
            (raw.get("settings") or {}).get("duration"), "%s duration" % label, 5, 3600, 60)},
    }
    if kind == "poll":
        out["options"] = _labelled_list(raw.get("options"), "%s options" % label, "label", 80, 2)
    elif kind == "emoji":
        out["options"] = _labelled_list(raw.get("options"), "%s reactions" % label, "char", 8, 1)
    elif kind == "ranking":
        out["items"] = _labelled_list(raw.get("items"), "%s items" % label, "label", 80, 2)
    elif kind == "slider":
        out["leftLabel"] = _text(raw.get("leftLabel"), "%s left label" % label, 40)
        out["rightLabel"] = _text(raw.get("rightLabel"), "%s right label" % label, 40)
        out["resultLabel"] = _text(raw.get("resultLabel"), "", 40, required=False)
    return out


def validate_event(payload):
    """Untrusted payload -> a clean event config. Raises Invalid with a message
    meant to be shown to whoever is filling in the form.

    A topic is a discussion question (always live, never timed) plus an ordered
    list of interactions the moderator can launch while on that topic."""
    if not isinstance(payload, dict):
        raise Invalid("Malformed request")

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise Invalid("Add at least one topic")
    if len(raw_topics) > MAX_TOPICS:
        raise Invalid("An event allows at most %d topics" % MAX_TOPICS)

    topics = []
    for i, raw in enumerate(raw_topics):
        if not isinstance(raw, dict):
            raise Invalid("Topic %d is malformed" % (i + 1))
        label = "Topic %d" % (i + 1)

        raw_items = raw.get("interactions") or []
        if not isinstance(raw_items, list):
            raise Invalid("%s items are malformed" % label)
        if len(raw_items) > MAX_INTERACTIONS:
            raise Invalid("%s allows at most %d items" % (label, MAX_INTERACTIONS))

        topics.append({
            "id": "t%d" % (i + 1),
            "question": _text(raw.get("question"), "%s question" % label, 200),
            "settings": {"whatsNextThreshold": _int(
                (raw.get("settings") or {}).get("whatsNextThreshold"),
                "%s what's-next threshold" % label, 1, 1000, 10)},
            "interactions": [_validate_interaction(item, i + 1, n) for n, item in enumerate(raw_items)],
        })

    return {
        "brand": _text(payload.get("brand"), "Brand", 40),
        "eventName": _text(payload.get("eventName"), "Event name", 60),
        "topics": topics,
    }


def slugify(text):
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_" and out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:40]


def _event_path(event_id):
    """Only ever called with an id already present in EVENTS (i.e. one this
    server itself discovered via listdir), so the join cannot be steered."""
    return os.path.join(EVENTS_DIR, event_id + ".json")


def save_event(config):
    """Write a validated config to events/<slug>.json under a name that is
    unique and, because it is rebuilt from scratch, always inside EVENTS_DIR."""
    base = slugify(config["eventName"]) or "event"
    with LOCK:
        slug, n = base, 2
        while slug in EVENTS or os.path.exists(os.path.join(EVENTS_DIR, slug + ".json")):
            slug, n = "%s-%d" % (base, n), n + 1
        path = os.path.join(EVENTS_DIR, slug + ".json")
        with open(path, "w") as fh:
            json.dump(config, fh, indent=2)
        EVENTS[slug] = config
    return slug


SEED_BY_OPTION = {"poll": ("votes", "options"), "emoji": ("counts", "options"),
                  "ranking": ("scores", "items")}


def _remap_seed(prev, item, seed):
    """Option ids are regenerated on every save, so a preserved seed keyed by the
    old ids has to be re-keyed onto the new ones (matched by position) or its
    numbers would silently reset to zero."""
    entry = SEED_BY_OPTION.get(item.get("kind"))
    if not entry:
        return seed
    seed_key, list_key = entry
    counts = seed.get(seed_key)
    if not isinstance(counts, dict):
        return seed
    old_ids = [o["id"] for o in prev.get(list_key, [])]
    new_ids = [o["id"] for o in item.get(list_key, [])]
    remapped = {}
    for i, new_id in enumerate(new_ids):
        if i < len(old_ids) and old_ids[i] in counts:
            remapped[new_id] = counts[old_ids[i]]
    seed = dict(seed)
    seed[seed_key] = remapped
    return seed


def merge_preserved(old, new):
    """Carry over the parts of an event the setup form does not edit — the demo
    room's seed blocks — so saving an edit never silently discards them."""
    for key in ("seedChallenges", "seedSimParticipants"):
        if key in old:
            new[key] = old[key]
    old_topics = old.get("topics", [])
    for i, topic in enumerate(new["topics"]):
        if i >= len(old_topics):
            break
        prev_topic = old_topics[i]
        if "seed" in prev_topic:
            topic["seed"] = prev_topic["seed"]
        prev_items = prev_topic.get("interactions", [])
        for n, item in enumerate(topic["interactions"]):
            if n >= len(prev_items):
                break
            prev_item = prev_items[n]
            if prev_item.get("kind") == item.get("kind") and "seed" in prev_item:
                item["seed"] = _remap_seed(prev_item, item, prev_item["seed"])
    return new


def update_event(event_id, config):
    with LOCK:
        config = merge_preserved(EVENTS[event_id], config)
        with open(_event_path(event_id), "w") as fh:
            json.dump(config, fh, indent=2)
        EVENTS[event_id] = config
    return config


def delete_event(event_id):
    with LOCK:
        os.remove(_event_path(event_id))
        EVENTS.pop(event_id, None)


# ---------------------------------------------------------------------------
# Room registry + persistence
# ---------------------------------------------------------------------------

ROOMS = {}
_dirty = False


def mark_dirty():
    global _dirty
    _dirty = True


def reset_room(room):
    """Wipe every tally back to zero, keeping the room, its code and its event.
    Used to clear a rehearsal before the doors open — note this always clears
    fully, including the demo seed numbers, so a reset room starts truly empty."""
    room["topicRuntime"] = [_init_topic_runtime(t, seed=False) for t in room["topics"]]
    room["challenges"] = []
    room["invited"] = []
    room["simParticipants"] = 0
    _activate_topic(room, 0)
    mark_dirty()


def get_room(code):
    """Look up a room. Rooms are only brought into being by open_room() from the
    setup page, so a stray ?room=CODE from a stale tab or a mistyped code can no
    longer conjure one mid-event."""
    with LOCK:
        return ROOMS.get(sanitize_code(code))


def open_room(code, event_id=None, seed=None):
    """Create a room deliberately — the setup page's OPEN ROOM, or the demo room
    at startup. Returns the existing room untouched if the code is already live."""
    code = sanitize_code(code)
    with LOCK:
        room = ROOMS.get(code)
        if room is None:
            if seed is None:
                seed = code == DEFAULT_ROOM
            room = new_room(code, event_id or DEFAULT_EVENT_ID, seed=seed)
            ROOMS[code] = room
            mark_dirty()
        return room


def save_state():
    global _dirty
    with LOCK:
        if not _dirty:
            return
        data = {"rooms": ROOMS}
        _dirty = False
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print("  (persist failed: %s)" % exc)


def load_state():
    if not os.path.isfile(STATE_FILE):
        open_room(DEFAULT_ROOM)  # the demo room is always there
        return
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
        loaded = data.get("rooms", {})
        with LOCK:
            for code, room in loaded.items():
                ROOMS[sanitize_code(code)] = room
        if DEFAULT_ROOM not in ROOMS:
            open_room(DEFAULT_ROOM)
        print("  Restored %d room(s) from %s" % (len(ROOMS), os.path.basename(STATE_FILE)))
    except Exception as exc:
        print("  (could not load saved state: %s — starting fresh)" % exc)
        ROOMS.clear()
        open_room(DEFAULT_ROOM)


# ---------------------------------------------------------------------------
# SSE subscribers (scoped per room)
# ---------------------------------------------------------------------------

SUBSCRIBERS = {}   # sid -> {"q": Queue, "role": str, "code": str}
SUB_LOCK = threading.Lock()


def add_subscriber(role, code, crew=False):
    sid = uuid.uuid4().hex
    q = queue.Queue(maxsize=50)
    with SUB_LOCK:
        SUBSCRIBERS[sid] = {"q": q, "role": role, "code": code, "crew": crew}
    return sid, q


def remove_subscriber(sid):
    with SUB_LOCK:
        SUBSCRIBERS.pop(sid, None)


def audience_online(code):
    with SUB_LOCK:
        return sum(1 for s in SUBSCRIBERS.values() if s["role"] == "audience" and s["code"] == code)


def broadcast(code):
    """Two payloads go out: the crew sees live figures, everyone else sees an
    unrevealed poll/slider with its numbers stripped."""
    public = snapshot(code, crew=False)
    if public is None:
        return
    public_data = json.dumps(public)
    crew_data = None
    with SUB_LOCK:
        subs = [s for s in SUBSCRIBERS.values() if s["code"] == code]
    if any(s.get("crew") for s in subs):
        crew_data = json.dumps(snapshot(code, crew=True))
    for s in subs:
        try:
            s["q"].put_nowait(crew_data if s.get("crew") else public_data)
        except queue.Full:
            pass


def _discussion_payload(rt):
    """The current topic's discussion tallies. Always present — every topic has a
    discussion, and it is what the room falls back to between interactions."""
    s = rt["sentiment"]
    total = max(1, s["agree"] + s["disagree"] + s["unsure"])
    sentiment = {
        "agree": s["agree"], "disagree": s["disagree"], "unsure": s["unsure"],
        "agreePct": round(100 * s["agree"] / total),
        "disagreePct": round(100 * s["disagree"] / total),
        "unsurePct": round(100 * s["unsure"] / total),
    }
    wn = rt["whatsNext"]
    whats_next = {
        "votes": wn["votes"], "threshold": wn["threshold"],
        "remaining": max(0, wn["threshold"] - wn["votes"]),
        "unlocked": wn["votes"] >= wn["threshold"],
    }
    return sentiment, rt["sentimentHistory"], whats_next, rt["responses"]


EMPTY_PAYLOADS = {
    "poll": {"question": "", "options": []},
    "wordcloud": {"question": "", "words": []},
    "emoji": {"question": "", "reactions": []},
    "slider": {"question": "", "leftLabel": "", "rightLabel": "", "resultLabel": "", "avg": 0, "count": 0},
    "ranking": {"question": "", "items": []},
}


def _interaction_payload(item, rt):
    """Render one interaction for the wire, paired with its response count."""
    kind = item["kind"]
    if kind == "poll":
        total = max(1, sum(rt["votes"].values()))
        options = [{"id": o["id"], "label": o["label"], "votes": rt["votes"][o["id"]],
                    "pct": round(100 * rt["votes"][o["id"]] / total)} for o in item["options"]]
        return {"question": item["question"], "options": options}, sum(rt["votes"].values())
    if kind == "wordcloud":
        words = [{"text": w, "weight": c} for w, c in rt["words"].items()]
        return {"question": item["question"], "words": words}, sum(rt["words"].values())
    if kind == "emoji":
        reactions = [{"id": o["id"], "char": o["char"], "count": rt["counts"][o["id"]]} for o in item["options"]]
        return {"question": item["question"], "reactions": reactions}, sum(rt["counts"].values())
    if kind == "slider":
        avg = round(rt["sum"] / rt["count"]) if rt["count"] else 0
        return {
            "question": item["question"],
            "leftLabel": item.get("leftLabel", ""), "rightLabel": item.get("rightLabel", ""),
            "resultLabel": item.get("resultLabel", ""),
            "avg": avg, "count": rt["count"],
        }, rt["count"]
    if kind == "ranking":
        items = sorted(
            ({"id": i["id"], "label": i["label"], "score": rt["scores"][i["id"]]} for i in item["items"]),
            key=lambda i: -i["score"])
        return {"question": item["question"], "items": items}, rt.get("submissions", 0)
    return {}, 0


def _conceal(payload, kind):
    """Strip the numbers out of an unrevealed poll/slider. The figures are
    withheld here rather than hidden in CSS, so a guest reading the network
    traffic can't peek at the result before the room sees it."""
    hidden = dict(payload)
    if kind == "poll":
        hidden["options"] = [{"id": o["id"], "label": o["label"], "votes": None, "pct": None}
                             for o in payload["options"]]
    elif kind == "slider":
        hidden["avg"] = None
    return hidden


def missing_snapshot(code):
    """A room code nobody opened. Same shape as a real snapshot so every surface
    can render it without special-casing every field — just `exists: false`."""
    payloads = dict(EMPTY_PAYLOADS)
    return {
        "exists": False,
        "code": sanitize_code(code),
        "brand": "THE UPGRADE", "eventName": "",
        "closed": False, "joinUrl": join_url(code),
        "topic": "", "topicIndex": 0, "topicCount": 0,
        "interactions": [], "activeInteraction": None,
        "mode": "discussion", "revealed": True, "revealable": False,
        "timed": False, "paused": False, "timeRemaining": 0,
        "inRoom": 0, "responses": 0,
        "sentiment": {"agree": 0, "disagree": 0, "unsure": 0,
                      "agreePct": 0, "disagreePct": 0, "unsurePct": 0},
        "sentimentHistory": [],
        "whatsNext": {"votes": 0, "threshold": 10, "remaining": 10, "unlocked": False},
        "challenges": [], "invited": [],
        "poll": payloads["poll"], "wordcloud": payloads["wordcloud"],
        "emoji": payloads["emoji"], "slider": payloads["slider"],
        "ranking": payloads["ranking"],
    }


def snapshot(code, crew=False):
    with LOCK:
        r = ROOMS.get(sanitize_code(code))
        if r is None:
            return missing_snapshot(code)
        in_room = r["simParticipants"] + audience_online(r["code"])
        topic = r["topics"][r["topicIndex"]]
        rt = r["topicRuntime"][r["topicIndex"]]
        items = topic.get("interactions", [])
        active = r.get("activeInteraction")

        sentiment, sentiment_history, whats_next, disc_responses = _discussion_payload(rt)

        # Only the live interaction carries content; the other screens are sent
        # as empty shells so each surface can render whichever one is showing
        # without needing to know what else the topic has configured.
        payloads = dict(EMPTY_PAYLOADS)
        responses = disc_responses
        revealed = r.get("revealed", True)
        if active is not None and 0 <= active < len(items):
            kind = items[active]["kind"]
            payload, responses = _interaction_payload(items[active], rt["interactions"][active])
            if not revealed and not crew:
                payload = _conceal(payload, kind)
            payloads[kind] = payload

        return {
            "exists": True,
            "code": r["code"],
            "brand": r["brand"],
            "eventName": r["eventName"],
            "closed": r.get("closed", False),
            "joinUrl": join_url(r["code"]),
            "topic": topic.get("question", ""),
            "topicIndex": r["topicIndex"],
            "topicCount": r["topicCount"],
            # what this topic offers, so the moderator can mirror the setup
            "interactions": [{"index": n, "kind": i["kind"], "question": i["question"],
                              "duration": i.get("settings", {}).get("duration", 60),
                              "live": n == active}
                             for n, i in enumerate(items)],
            "activeInteraction": active,
            "mode": r["mode"],
            "revealed": revealed,
            "revealable": active is not None and items[active]["kind"] in HIDEABLE and not revealed,
            "timed": active is not None and r["mode"] != "results",
            "paused": r["paused"],
            "timeRemaining": r["timeRemaining"],
            "inRoom": in_room,
            "responses": responses,
            "sentiment": sentiment,
            "sentimentHistory": sentiment_history,
            "whatsNext": whats_next,
            "challenges": r["challenges"],
            "invited": r["invited"],
            "poll": payloads["poll"],
            "wordcloud": payloads["wordcloud"],
            "emoji": payloads["emoji"],
            "slider": payloads["slider"],
            "ranking": payloads["ranking"],
        }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

AUDIENCE_ACTIONS = ("join", "sentiment", "whatsnext", "challenge", "poll", "word",
                    "emoji", "slider", "ranking")


def act(code, kind, pid, data):
    with LOCK:
        r = get_room(code)
        if r is None:
            return False   # no such room — nothing to act on
        if r.get("closed") and kind in AUDIENCE_ACTIONS:
            return  # a closed room stops taking part; tallies are left as they are

        topic = r["topics"][r["topicIndex"]]
        rt = r["topicRuntime"][r["topicIndex"]]
        items = topic.get("interactions", [])
        active = r.get("activeInteraction")
        # the live interaction's config + runtime, or (None, None) on discussion
        item = items[active] if active is not None and active < len(items) else None
        irt = rt["interactions"][active] if item is not None else None

        def live(kind_name):
            return item is not None and item["kind"] == kind_name and r["mode"] != "results"

        if kind == "join":
            pass

        elif kind == "sentiment":
            choice = data.get("choice")
            if choice in ("agree", "disagree", "unsure"):
                prev = rt["_votes"]["sentiment"].get(pid)
                if prev != choice:
                    if prev in rt["sentiment"]:
                        rt["sentiment"][prev] = max(0, rt["sentiment"][prev] - 1)
                    else:
                        rt["responses"] += 1
                    rt["sentiment"][choice] += 1
                    rt["_votes"]["sentiment"][pid] = choice
                    _push_history(rt)

        elif kind == "whatsnext":
            if pid not in rt["_votes"]["next"]:
                rt["_votes"]["next"].append(pid)
                rt["whatsNext"]["votes"] += 1

        elif kind == "challenge":
            text = (data.get("text") or "").strip()[:180]
            name = (data.get("name") or "").strip()[:40]
            if text:
                r["challenges"].insert(0, {
                    "id": _cid(), "name": name or "Anonymous",
                    "initials": _initials(name) if name else "?", "text": text, "at": time.time(),
                })
                r["challenges"] = r["challenges"][:24]

        elif kind == "poll":
            if live("poll"):
                opt = data.get("option")
                if opt in irt["votes"]:
                    prev = irt["_votes"].get(pid)
                    if prev != opt:
                        if prev in irt["votes"]:
                            irt["votes"][prev] = max(0, irt["votes"][prev] - 1)
                        irt["votes"][opt] += 1
                        irt["_votes"][pid] = opt

        elif kind == "word":
            if live("wordcloud"):
                word = (data.get("word") or "").strip().lower()[:20]
                word = "".join(ch for ch in word if ch.isalnum() or ch in "-'")
                if word:
                    irt["words"][word] = irt["words"].get(word, 0) + 1

        elif kind == "emoji":
            if live("emoji"):
                oid = data.get("id")
                if oid in irt["counts"]:
                    irt["counts"][oid] += 1

        elif kind == "slider":
            if live("slider"):
                val = data.get("value")
                try:
                    val = max(0, min(100, int(val)))
                except (TypeError, ValueError):
                    val = None
                if val is not None:
                    prev = irt["_votes"].get(pid)
                    if prev is None:
                        irt["count"] += 1
                    else:
                        irt["sum"] -= prev
                    irt["sum"] += val
                    irt["_votes"][pid] = val

        elif kind == "ranking":
            if live("ranking"):
                order = data.get("order") or []
                n = len(order)
                if n and all(i in irt["scores"] for i in order):
                    for pos, iid in enumerate(order):
                        irt["scores"][iid] += (n - pos)
                    irt["submissions"] = irt.get("submissions", 0) + 1

        # ---- moderator: topic flow ----
        elif kind == "launchInteraction":
            try:
                _activate_interaction(r, int(data.get("index")))
            except (TypeError, ValueError):
                pass
        elif kind == "reveal":
            r["revealed"] = True
        elif kind == "backToDiscussion":
            r["activeInteraction"] = None
            r["mode"] = "discussion"
            r["paused"] = False
            r["timeRemaining"] = 0
        elif kind == "showResults":
            r["mode"] = "results"
            r["paused"] = True
        elif kind == "nextTopic":
            if r["topicIndex"] < r["topicCount"] - 1:
                _activate_topic(r, r["topicIndex"] + 1)
        elif kind == "prevTopic":
            if r["topicIndex"] > 0:
                _activate_topic(r, r["topicIndex"] - 1)
        elif kind == "extendTime":
            if active is not None:
                r["timeRemaining"] += 30
        elif kind == "togglePause":
            if active is not None:
                r["paused"] = not r["paused"]
        elif kind == "invite":
            cid = data.get("id")
            if cid and cid not in r["invited"]:
                r["invited"].append(cid)
        elif kind == "inviteTop":
            if r["challenges"]:
                cid = r["challenges"][0]["id"]
                if cid not in r["invited"]:
                    r["invited"].append(cid)

        mark_dirty()
    broadcast(sanitize_code(code))


def _initials(name):
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _push_history(rt):
    s = rt["sentiment"]
    total = max(1, s["agree"] + s["disagree"] + s["unsure"])
    rt["sentimentHistory"].append({
        "agree": round(100 * s["agree"] / total),
        "disagree": round(100 * s["disagree"] / total),
    })
    rt["sentimentHistory"] = rt["sentimentHistory"][-24:]


# ---------------------------------------------------------------------------
# Background ticker: countdown + periodic persistence + SSE keepalive.
# ---------------------------------------------------------------------------

def ticker():
    n = 0
    while True:
        time.sleep(1)
        n += 1
        with LOCK:
            codes = list(ROOMS.keys())
            for code in codes:
                r = ROOMS[code]
                # only a live interaction counts down; a topic's discussion has
                # no timer and simply stays up until the moderator acts
                timed = r.get("activeInteraction") is not None and r["mode"] != "results"
                if timed and not r["paused"] and not r.get("closed") and r["timeRemaining"] > 0:
                    r["timeRemaining"] -= 1
                    mark_dirty()
        for code in codes:
            broadcast(code)
        if n % 5 == 0:
            save_state()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json",
}
PAGES = {"/": "audience.html", "/moderator": "moderator.html",
         "/projector": "projector.html", "/setup": "setup.html"}
# The crew surfaces. The audience page and the projector stay open: the projector
# is a passive display, often on a machine nobody can type on.
PROTECTED_PAGES = ("/moderator", "/setup")

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Upgrade LIVE — Crew</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Space+Mono:wght@400;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/css/app.css">
<style>
body{display:flex;align-items:center;justify-content:center;min-height:100dvh;padding:28px;
  background:radial-gradient(120% 60% at 50% 0%, rgba(255,45,70,.10), transparent 60%),#0a0a0b;}
.card{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);
  border-radius:22px;padding:30px 26px;text-align:center;}
.eyebrow{font-family:var(--mono);color:var(--red);letter-spacing:.28em;font-size:12px;font-weight:700;}
h1{font-family:var(--display);text-transform:uppercase;font-size:44px;line-height:.92;margin:14px 0 6px;color:var(--ink);}
h1 b{color:var(--red);font-weight:400;display:block;}
p{color:var(--muted);font-size:14.5px;line-height:1.5;margin:0 0 22px;}
input.code{width:100%;background:#0d0d0f;border:1px solid var(--line-2);border-radius:14px;color:var(--ink);
  padding:16px;font-size:22px;outline:none;text-align:center;font-family:var(--mono);letter-spacing:.3em;}
input.code:focus{border-color:var(--red);}
button{width:100%;margin-top:14px;}
.err{color:var(--red);font-family:var(--mono);font-size:12px;letter-spacing:.1em;margin-top:14px;min-height:16px;}
</style></head>
<body>
<form class="card" method="POST" action="/login">
  <div class="eyebrow">CREW ONLY</div>
  <h1>THE<b>UPGRADE</b></h1>
  <p>Enter the passcode to reach the control room and event setup.</p>
  <input class="code" name="passcode" type="password" inputmode="numeric"
         autocomplete="current-password" autofocus aria-label="Passcode" placeholder="******">
  <input type="hidden" name="next" value="__NEXT__">
  <button class="btn" type="submit">UNLOCK &rarr;</button>
  <div class="err">__ERROR__</div>
</form>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---- passcode ------------------------------------------------------
    def _cookie_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def authed(self):
        return valid_session(self._cookie_token())

    def _send_login(self, next_path="/setup", error=""):
        page = (LOGIN_PAGE
                .replace("__NEXT__", html_escape(next_path))
                .replace("__ERROR__", html_escape(error)))
        return self._send(200, page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def _deny(self):
        return self._send(401, json.dumps({"ok": False, "error": "Passcode required — reload and sign in."}),
                          "application/json", {"Cache-Control": "no-store"})

    def _redirect(self, location, cookie=None, clear_cookie=False):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie",
                             "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400" % (COOKIE, cookie))
        if clear_cookie:
            self.send_header("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE)
        self.end_headers()

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, val in extra.items():
                self.send_header(k, val)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/login":
            if self.authed():
                return self._redirect("/setup")
            return self._send_login(parse_qs(parsed.query).get("next", ["/setup"])[0])
        if path == "/logout":
            token = self._cookie_token()
            with SESSION_LOCK:
                SESSIONS.discard(token)
            return self._redirect("/login", clear_cookie=True)
        if path in PROTECTED_PAGES and not self.authed():
            # keep the query (?room=…) so signing in returns to the right room
            return self._send_login(self.path)
        if path == "/api/events" and not self.authed():
            return self._deny()
        if path.startswith("/api/events/") and not self.authed():
            return self._deny()
        if path == "/api/rooms" and not self.authed():
            return self._deny()
        if path == "/events":
            return self.handle_sse(parsed)
        if path == "/api/state":
            qs = parse_qs(parsed.query)
            code = qs.get("room", [DEFAULT_ROOM])[0]
            return self._send(200, json.dumps(snapshot(code, crew=self.authed())), "application/json",
                              {"Cache-Control": "no-store"})
        if path.startswith("/api/leads/"):
            if not self.authed():
                return self._deny()
            code = sanitize_code(path[len("/api/leads/"):].replace(".json", ""))
            body = json.dumps(read_leads(code), indent=2)
            return self._send(200, body, "application/json", {
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="leads-%s.json"' % code,
            })
        if path == "/api/rooms":
            with LOCK:
                rooms = [{"code": c, "eventId": r.get("eventId", DEFAULT_EVENT_ID),
                          "eventName": r.get("eventName", ""), "closed": r.get("closed", False),
                          "leads": len(read_leads(c))}
                         for c, r in sorted(ROOMS.items())]
            return self._send(200, json.dumps({"rooms": rooms}), "application/json", {"Cache-Control": "no-store"})
        if path == "/api/events":
            with LOCK:
                payload = {"events": event_summaries(), "defaultId": DEFAULT_EVENT_ID}
            return self._send(200, json.dumps(payload), "application/json", {"Cache-Control": "no-store"})
        if path.startswith("/api/events/"):
            event_id = path[len("/api/events/"):]
            with LOCK:
                config = EVENTS.get(event_id)
                body = json.dumps({"ok": True, "id": event_id, "config": config}) if config else None
            if body is None:
                return self._send(404, json.dumps({"ok": False, "error": "Unknown event"}), "application/json")
            return self._send(200, body, "application/json", {"Cache-Control": "no-store"})
        if path in PAGES:
            return self.serve_static(PAGES[path])
        if path.startswith("/css/") or path.startswith("/js/") or path.startswith("/assets/"):
            return self.serve_static(path.lstrip("/"))
        if path == "/qr.svg":
            qs = parse_qs(parsed.query)
            code = sanitize_code(qs.get("room", [DEFAULT_ROOM])[0])
            try:
                svg = qr.render_svg(join_url(code), size_px=420)
            except ValueError as exc:
                return self._send(500, str(exc))
            return self._send(200, svg, "image/svg+xml", {"Cache-Control": "no-store"})
        if path == "/favicon.ico":
            return self._send(204, b"")
        return self._send(404, "Not found")

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/events/"):
            return self._send(404, "Not found")
        if not self.authed():
            return self._deny()
        event_id = path[len("/api/events/"):]
        with LOCK:
            if event_id not in EVENTS:
                return self._send(404, json.dumps({"ok": False, "error": "Unknown event"}), "application/json")
            if event_id == DEFAULT_EVENT_ID:
                return self._send(400, json.dumps(
                    {"ok": False, "error": "The demo event can't be deleted — it's the fallback for new rooms."}),
                    "application/json")
            try:
                delete_event(event_id)
            except OSError as exc:
                return self._send(500, json.dumps({"ok": False, "error": "Could not delete: %s" % exc}),
                                  "application/json")
        return self._send(200, json.dumps({"ok": True}), "application/json", {"Cache-Control": "no-store"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            return self.handle_login()

        is_event_update = parsed.path.startswith("/api/events/")
        is_room_update = parsed.path.startswith("/api/rooms/")
        if (parsed.path not in ("/api/action", "/api/events", "/api/rooms", "/api/leads")
                and not is_event_update and not is_room_update):
            return self._send(404, "Not found")
        # /api/action and /api/leads are guest-facing (actions are checked per
        # kind below); everything else is crew-only
        if parsed.path not in ("/api/action", "/api/leads") and not self.authed():
            return self._deny()
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 256 * 1024:
            return self._send(413, json.dumps({"ok": False, "error": "Payload too large"}), "application/json")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, json.dumps({"ok": False, "error": "Malformed request"}), "application/json")

        if is_event_update:
            return self.update_event_route(parsed.path[len("/api/events/"):], data)
        if is_room_update:
            return self.update_room_route(parsed.path[len("/api/rooms/"):], data)
        if parsed.path == "/api/events":
            return self.create_event(data)
        if parsed.path == "/api/rooms":
            return self.create_room(data)
        if parsed.path == "/api/leads":
            return self.capture_lead(data)

        kind = data.get("type")
        if kind not in AUDIENCE_ACTIONS and not self.authed():
            return self._deny()   # driving the show is crew-only
        code = data.get("room") or DEFAULT_ROOM
        if get_room(code) is None:
            return self._send(404, json.dumps({"ok": False, "error": "That room isn't open."}),
                              "application/json", {"Cache-Control": "no-store"})
        try:
            act(code, kind, data.get("pid") or "anon", data)
        except Exception as exc:
            return self._send(500, json.dumps({"ok": False, "error": str(exc)}), "application/json")
        return self._send(200, json.dumps({"ok": True}), "application/json", {"Cache-Control": "no-store"})

    def capture_lead(self, data):
        email = plausible_email(data.get("email"))
        if not email:
            return self._send(400, json.dumps({"ok": False, "error": "That doesn't look like an email address."}),
                              "application/json")
        name = (data.get("name") or "").strip()[:60]
        code = sanitize_code(data.get("room"))
        if get_room(code) is None:
            return self._send(404, json.dumps({"ok": False, "error": "That room isn't open."}),
                              "application/json")
        stored = add_lead(code, email, name)
        # an address that already signed up still gets a yes — from the guest's
        # side they are on the list either way
        return self._send(200, json.dumps({"ok": True, "duplicate": not stored}),
                          "application/json", {"Cache-Control": "no-store"})

    def handle_login(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 4096:
            return self._send(413, "Too large")
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        fields = parse_qs(body)
        given = (fields.get("passcode", [""])[0] or "").strip()
        next_path = fields.get("next", ["/setup"])[0] or "/setup"
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/setup"          # never bounce off-site
        # constant-time compare so the passcode can't be probed by timing
        if not hmac.compare_digest(given, PASSCODE):
            return self._send_login(next_path, "That passcode didn't match.")
        return self._redirect(next_path, cookie=new_session())

    def create_event(self, data):
        try:
            config = validate_event(data)
        except Invalid as exc:
            return self._send(400, json.dumps({"ok": False, "error": str(exc)}), "application/json")
        try:
            event_id = save_event(config)
        except OSError as exc:
            return self._send(500, json.dumps({"ok": False, "error": "Could not save event: %s" % exc}),
                              "application/json")
        body = {"ok": True, "eventId": event_id, "eventName": config["eventName"]}
        return self._send(200, json.dumps(body), "application/json", {"Cache-Control": "no-store"})

    def update_event_route(self, event_id, data):
        with LOCK:
            known = event_id in EVENTS
        if not known:
            return self._send(404, json.dumps({"ok": False, "error": "Unknown event"}), "application/json")
        try:
            config = validate_event(data)
        except Invalid as exc:
            return self._send(400, json.dumps({"ok": False, "error": str(exc)}), "application/json")
        try:
            config = update_event(event_id, config)
        except OSError as exc:
            return self._send(500, json.dumps({"ok": False, "error": "Could not save event: %s" % exc}),
                              "application/json")
        body = {"ok": True, "eventId": event_id, "eventName": config["eventName"]}
        return self._send(200, json.dumps(body), "application/json", {"Cache-Control": "no-store"})

    def update_room_route(self, code, data):
        """Turn a room off or back on. Closing keeps every tally, so the same
        room can be resumed exactly where it left off."""
        code = sanitize_code(code)
        with LOCK:
            room = ROOMS.get(code)
            if room is None:
                return self._send(404, json.dumps({"ok": False, "error": "Unknown room"}), "application/json")
            if data.get("reset"):
                reset_room(room)
            if "closed" in data:
                room["closed"] = bool(data.get("closed"))
            mark_dirty()
            closed = room["closed"]
        broadcast(code)
        return self._send(200, json.dumps({"ok": True, "code": code, "closed": closed}),
                          "application/json", {"Cache-Control": "no-store"})

    def create_room(self, data):
        code = sanitize_code(data.get("code"))
        event_id = data.get("eventId")
        with LOCK:
            if event_id not in EVENTS:
                return self._send(400, json.dumps({"ok": False, "error": "Unknown event"}), "application/json")
            if code in ROOMS:
                return self._send(409, json.dumps(
                    {"ok": False, "error": "Room %s is already in use" % code}), "application/json")
            open_room(code, event_id, seed=False)
        # surfaces already parked on this code flip from "not open" to live
        broadcast(code)
        body = {"ok": True, "code": code, "eventId": event_id}
        return self._send(200, json.dumps(body), "application/json", {"Cache-Control": "no-store"})

    def serve_static(self, rel):
        rel = rel.replace("..", "")
        full = os.path.join(PUBLIC, rel)
        if not os.path.isfile(full):
            return self._send(404, "Not found")
        ctype = STATIC_TYPES.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype, {"Cache-Control": "no-cache"})

    def handle_sse(self, parsed):
        qs = parse_qs(parsed.query)
        role = qs.get("role", ["audience"])[0]
        if role not in ("audience", "moderator", "projector"):
            role = "audience"
        code = sanitize_code(qs.get("room", [DEFAULT_ROOM])[0])
        # note: no room is created here — an unknown code just streams the
        # "no such room" snapshot until the crew opens it from setup
        # the crew view is decided by the passcode cookie, never by the
        # client-supplied role — otherwise ?role=moderator would leak results
        crew = self.authed()
        sid, q = add_subscriber(role, code, crew=crew)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            broadcast(code)  # refresh in-room count for everyone
            self.wfile.write(self._sse(json.dumps(snapshot(code, crew=crew))))
            self.wfile.flush()
            while True:
                try:
                    self.wfile.write(self._sse(q.get(timeout=15)))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            remove_subscriber(sid)
            broadcast(code)

    @staticmethod
    def _sse(data):
        return ("data: " + data + "\n\n").encode("utf-8")


def main():
    load_events()
    if DEFAULT_EVENT_ID not in EVENTS:
        raise SystemExit("  No events/%s.json found — cannot start." % DEFAULT_EVENT_ID)
    print("\n  THE UPGRADE LIVE  —  real-time server running")
    print("  Loaded %d event(s): %s" % (len(EVENTS), ", ".join(sorted(EVENTS))))
    load_state()
    threading.Thread(target=ticker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    host = lan_host()
    print("  • Set up     http://localhost:%d/setup       (crew)" % PORT)
    print("  • Moderator  http://localhost:%d/moderator   (crew)" % PORT)
    print("  • Projector  http://localhost:%d/projector" % PORT)
    print("  • Audience   http://%s:%d/   ← what phones scan" % (host, PORT))
    print("\n  CREW PASSCODE: %s%s" % (
        PASSCODE, "" if os.environ.get("PASSCODE") else "   (set PASSCODE=… to choose your own)"))
    print("  Default room: %s   (add ?room=CODE for others)   Ctrl+C to stop\n" % DEFAULT_ROOM)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Saving & stopping…")
        mark_dirty()
        save_state()
        server.shutdown()


if __name__ == "__main__":
    main()
