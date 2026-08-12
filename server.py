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
    • Multi-room   — independent rooms keyed by code, auto-created on demand.
                     WN25 is seeded to match the mockups; other rooms start fresh.
    • Persistence  — every room is saved to rooms_state.json and reloaded on start.
    • Real-time    — Server-Sent Events (/events) push each room's snapshot to its
                     surfaces; clients send actions via POST /api/action.
"""

import json
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
STATE_FILE = os.path.join(ROOT, "rooms_state.json")
DEFAULT_ROOM = "WN25"

LOCK = threading.RLock()
MODES = ("discussion", "poll", "wordcloud", "emoji", "slider", "ranking", "results")

TOPICS = [
    "Is convenience making us weaker?",
    "Should AI have creative rights?",
    "Is privacy already dead?",
    "Will remote work outlast the office?",
    "Can technology fix loneliness?",
]


def _cid():
    return uuid.uuid4().hex[:8]


def sanitize_code(code):
    code = "".join(ch for ch in (code or "") if ch.isalnum()).upper()[:8]
    return code or DEFAULT_ROOM


def new_room(code, seed=False):
    """A room. When seed=True it is populated to match the design mockups;
    otherwise it starts with the same structure but zeroed counts (a fresh debate)."""
    room = {
        "code": code,
        "brand": "THE UPGRADE",
        "eventName": "THE BIG DEBATE",
        "topics": TOPICS,
        "topicIndex": 0,
        "topicCount": len(TOPICS),
        "mode": "discussion",
        "paused": False,
        "timeRemaining": 138,
        "timeInitial": 138,
        "sentiment": {"agree": 41, "disagree": 37, "unsure": 22} if seed else {"agree": 0, "disagree": 0, "unsure": 0},
        "sentimentHistory": [
            {"agree": 44, "disagree": 34}, {"agree": 43, "disagree": 35},
            {"agree": 45, "disagree": 33}, {"agree": 42, "disagree": 36},
            {"agree": 43, "disagree": 35}, {"agree": 41, "disagree": 37},
            {"agree": 42, "disagree": 37}, {"agree": 41, "disagree": 37},
        ] if seed else [],
        "whatsNext": {"votes": 7 if seed else 0, "threshold": 10},
        "challenges": [
            {"id": _cid(), "name": "Alex R.", "initials": "AR",
             "text": "I disagree — convenience has freed up time for creativity.", "at": time.time() - 60},
            {"id": _cid(), "name": "Maya L.", "initials": "ML",
             "text": "But at what cost to our attention and focus?", "at": time.time() - 120},
            {"id": _cid(), "name": "Jordan T.", "initials": "JT",
             "text": "Convenience is a tool, not the problem.", "at": time.time() - 180},
            {"id": _cid(), "name": "Sana K.", "initials": "SK",
             "text": "We optimized comfort and lost resilience.", "at": time.time() - 240},
            {"id": _cid(), "name": "Dev P.", "initials": "DP",
             "text": "Every generation says this about new tech.", "at": time.time() - 300},
            {"id": _cid(), "name": "Riya N.", "initials": "RN",
             "text": "Weaker how? Define the metric first.", "at": time.time() - 360},
        ] if seed else [],
        "invited": [],
        "poll": {
            "question": "Biggest challenge ahead?",
            "options": [
                {"id": "ai", "label": "AI & technology", "votes": 42 if seed else 0},
                {"id": "climate", "label": "Climate change", "votes": 28 if seed else 0},
                {"id": "mental", "label": "Mental health", "votes": 17 if seed else 0},
                {"id": "econ", "label": "Economic inequality", "votes": 13 if seed else 0},
            ],
        },
        "wordcloud": {
            "question": "One word for the future?",
            "words": {
                "exciting": 24, "uncertain": 19, "opportunity": 21, "overwhelming": 14,
                "hopeful": 12, "innovative": 11, "optimistic": 10, "limitless": 13,
                "changing": 9, "freedom": 12, "scary": 7, "unknown": 6,
                "automation": 6, "fragile": 5,
            } if seed else {},
        },
        "emoji": {
            "question": "How do you feel?",
            "reactions": [
                {"char": "\U0001F525", "count": 18 if seed else 0},
                {"char": "\U0001F44F", "count": 12 if seed else 0},
                {"char": "\U0001F914", "count": 9 if seed else 0},
                {"char": "❤️", "count": 22 if seed else 0},
                {"char": "\U0001F440", "count": 15 if seed else 0},
                {"char": "\U0001F64C", "count": 10 if seed else 0},
                {"char": "\U0001F62C", "count": 6 if seed else 0},
                {"char": "\U0001F389", "count": 4 if seed else 0},
                {"char": "\U0001F4A1", "count": 3 if seed else 0},
            ],
        },
        "slider": {
            "question": "AI replacing human jobs?",
            "leftLabel": "not worried", "rightLabel": "very", "resultLabel": "very concerned",
            "sum": 72 * 50 if seed else 0, "count": 50 if seed else 0,
        },
        "ranking": {
            "question": "Rank by future happiness",
            "items": [
                {"id": "rel", "label": "Relationships", "score": 500 if seed else 0},
                {"id": "health", "label": "Health", "score": 460 if seed else 0},
                {"id": "purpose", "label": "Purpose / Meaning", "score": 420 if seed else 0},
                {"id": "money", "label": "Money", "score": 300 if seed else 0},
                {"id": "success", "label": "Success", "score": 240 if seed else 0},
            ],
        },
        "responses": 50 if seed else 0,
        "simParticipants": 49 if seed else 0,
        # per-participant vote tracking (kept internal, never sent to clients)
        "_votes": {"sentiment": {}, "next": [], "poll": {}, "slider": {}},
    }
    return room


# ---------------------------------------------------------------------------
# Room registry + persistence
# ---------------------------------------------------------------------------

ROOMS = {}
_dirty = False


def mark_dirty():
    global _dirty
    _dirty = True


def get_room(code, create=True):
    code = sanitize_code(code)
    with LOCK:
        room = ROOMS.get(code)
        if room is None and create:
            room = new_room(code, seed=(code == DEFAULT_ROOM))
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
        get_room(DEFAULT_ROOM)  # seed default
        return
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
        loaded = data.get("rooms", {})
        with LOCK:
            for code, room in loaded.items():
                room.setdefault("_votes", {"sentiment": {}, "next": [], "poll": {}, "slider": {}})
                ROOMS[sanitize_code(code)] = room
        if DEFAULT_ROOM not in ROOMS:
            get_room(DEFAULT_ROOM)
        print("  Restored %d room(s) from %s" % (len(ROOMS), os.path.basename(STATE_FILE)))
    except Exception as exc:
        print("  (could not load saved state: %s — starting fresh)" % exc)
        ROOMS.clear()
        get_room(DEFAULT_ROOM)


# ---------------------------------------------------------------------------
# SSE subscribers (scoped per room)
# ---------------------------------------------------------------------------

SUBSCRIBERS = {}   # sid -> {"q": Queue, "role": str, "code": str}
SUB_LOCK = threading.Lock()


def add_subscriber(role, code):
    sid = uuid.uuid4().hex
    q = queue.Queue(maxsize=50)
    with SUB_LOCK:
        SUBSCRIBERS[sid] = {"q": q, "role": role, "code": code}
    return sid, q


def remove_subscriber(sid):
    with SUB_LOCK:
        SUBSCRIBERS.pop(sid, None)


def audience_online(code):
    with SUB_LOCK:
        return sum(1 for s in SUBSCRIBERS.values() if s["role"] == "audience" and s["code"] == code)


def broadcast(code):
    payload = snapshot(code)
    if payload is None:
        return
    data = json.dumps(payload)
    with SUB_LOCK:
        subs = [s for s in SUBSCRIBERS.values() if s["code"] == code]
    for s in subs:
        try:
            s["q"].put_nowait(data)
        except queue.Full:
            pass


def snapshot(code):
    with LOCK:
        r = ROOMS.get(sanitize_code(code))
        if r is None:
            return None
        s = r["sentiment"]
        total = max(1, s["agree"] + s["disagree"] + s["unsure"])
        poll_total = max(1, sum(o["votes"] for o in r["poll"]["options"]))
        in_room = r["simParticipants"] + audience_online(r["code"])
        ranked = sorted(r["ranking"]["items"], key=lambda i: -i["score"])
        slider_avg = round(r["slider"]["sum"] / max(1, r["slider"]["count"])) if r["slider"]["count"] else 0
        return {
            "code": r["code"],
            "brand": r["brand"],
            "eventName": r["eventName"],
            "topic": r["topics"][r["topicIndex"]],
            "topicIndex": r["topicIndex"],
            "topicCount": r["topicCount"],
            "mode": r["mode"],
            "paused": r["paused"],
            "timeRemaining": r["timeRemaining"],
            "inRoom": in_room,
            "responses": r["responses"],
            "sentiment": {
                "agree": s["agree"], "disagree": s["disagree"], "unsure": s["unsure"],
                "agreePct": round(100 * s["agree"] / total),
                "disagreePct": round(100 * s["disagree"] / total),
                "unsurePct": round(100 * s["unsure"] / total),
            },
            "sentimentHistory": r["sentimentHistory"],
            "whatsNext": {
                "votes": r["whatsNext"]["votes"],
                "threshold": r["whatsNext"]["threshold"],
                "remaining": max(0, r["whatsNext"]["threshold"] - r["whatsNext"]["votes"]),
                "unlocked": r["whatsNext"]["votes"] >= r["whatsNext"]["threshold"],
            },
            "challenges": r["challenges"],
            "invited": r["invited"],
            "poll": {
                "question": r["poll"]["question"],
                "options": [{**o, "pct": round(100 * o["votes"] / poll_total)} for o in r["poll"]["options"]],
            },
            "wordcloud": {
                "question": r["wordcloud"]["question"],
                "words": [{"text": w, "weight": c} for w, c in r["wordcloud"]["words"].items()],
            },
            "emoji": r["emoji"],
            "slider": {**r["slider"], "avg": slider_avg},
            "ranking": {"question": r["ranking"]["question"], "items": ranked},
        }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def act(code, kind, pid, data):
    with LOCK:
        r = get_room(code)
        v = r["_votes"]
        if kind == "join":
            pass

        elif kind == "sentiment":
            choice = data.get("choice")
            if choice in ("agree", "disagree", "unsure"):
                prev = v["sentiment"].get(pid)
                if prev != choice:
                    if prev in r["sentiment"]:
                        r["sentiment"][prev] = max(0, r["sentiment"][prev] - 1)
                    else:
                        r["responses"] += 1
                    r["sentiment"][choice] += 1
                    v["sentiment"][pid] = choice
                    _push_history(r)

        elif kind == "whatsnext":
            if pid not in v["next"]:
                v["next"].append(pid)
                r["whatsNext"]["votes"] += 1

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
            opt = data.get("option")
            options = {o["id"]: o for o in r["poll"]["options"]}
            if opt in options:
                prev = v["poll"].get(pid)
                if prev != opt:
                    if prev in options:
                        options[prev]["votes"] = max(0, options[prev]["votes"] - 1)
                    options[opt]["votes"] += 1
                    v["poll"][pid] = opt

        elif kind == "word":
            word = (data.get("word") or "").strip().lower()[:20]
            word = "".join(ch for ch in word if ch.isalnum() or ch in "-'")
            if word:
                r["wordcloud"]["words"][word] = r["wordcloud"]["words"].get(word, 0) + 1

        elif kind == "emoji":
            idx = data.get("index")
            reacts = r["emoji"]["reactions"]
            if isinstance(idx, int) and 0 <= idx < len(reacts):
                reacts[idx]["count"] += 1

        elif kind == "slider":
            val = data.get("value")
            try:
                val = max(0, min(100, int(val)))
            except (TypeError, ValueError):
                val = None
            if val is not None:
                prev = v["slider"].get(pid)
                if prev is None:
                    r["slider"]["count"] += 1
                else:
                    r["slider"]["sum"] -= prev
                r["slider"]["sum"] += val
                v["slider"][pid] = val

        elif kind == "ranking":
            order = data.get("order") or []
            items = {i["id"]: i for i in r["ranking"]["items"]}
            n = len(order)
            if n and all(i in items for i in order):
                for pos, iid in enumerate(order):
                    items[iid]["score"] += (n - pos)

        # ---- moderator ----
        elif kind == "setMode":
            if data.get("mode") in MODES:
                r["mode"] = data["mode"]
        elif kind == "nextTopic":
            if r["topicIndex"] < r["topicCount"] - 1:
                r["topicIndex"] += 1
                _reset_topic(r)
        elif kind == "prevTopic":
            if r["topicIndex"] > 0:
                r["topicIndex"] -= 1
                _reset_topic(r)
        elif kind == "extendTime":
            r["timeRemaining"] += 30
        elif kind == "togglePause":
            r["paused"] = not r["paused"]
        elif kind == "endTopic":
            r["mode"] = "results"
            r["paused"] = True
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


def _push_history(r):
    s = r["sentiment"]
    total = max(1, s["agree"] + s["disagree"] + s["unsure"])
    r["sentimentHistory"].append({
        "agree": round(100 * s["agree"] / total),
        "disagree": round(100 * s["disagree"] / total),
    })
    r["sentimentHistory"] = r["sentimentHistory"][-24:]


def _reset_topic(r):
    r["mode"] = "discussion"
    r["paused"] = False
    r["timeRemaining"] = r["timeInitial"]
    r["whatsNext"]["votes"] = 0
    r["_votes"]["next"] = []


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
                if not r["paused"] and r["timeRemaining"] > 0:
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
PAGES = {"/": "audience.html", "/moderator": "moderator.html", "/projector": "projector.html"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

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
        if path == "/events":
            return self.handle_sse(parsed)
        if path == "/api/state":
            qs = parse_qs(parsed.query)
            code = qs.get("room", [DEFAULT_ROOM])[0]
            get_room(code)
            return self._send(200, json.dumps(snapshot(code)), "application/json", {"Cache-Control": "no-store"})
        if path == "/api/rooms":
            with LOCK:
                codes = sorted(ROOMS.keys())
            return self._send(200, json.dumps({"rooms": codes}), "application/json", {"Cache-Control": "no-store"})
        if path in PAGES:
            return self.serve_static(PAGES[path])
        if path.startswith("/css/") or path.startswith("/js/") or path.startswith("/assets/"):
            return self.serve_static(path.lstrip("/"))
        if path == "/favicon.ico":
            return self._send(204, b"")
        return self._send(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/action":
            return self._send(404, "Not found")
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"ok": False}), "application/json")
        code = data.get("room") or DEFAULT_ROOM
        try:
            act(code, data.get("type"), data.get("pid") or "anon", data)
        except Exception as exc:
            return self._send(500, json.dumps({"ok": False, "error": str(exc)}), "application/json")
        return self._send(200, json.dumps({"ok": True}), "application/json", {"Cache-Control": "no-store"})

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
        get_room(code)
        sid, q = add_subscriber(role, code)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            broadcast(code)  # refresh in-room count for everyone
            self.wfile.write(self._sse(json.dumps(snapshot(code))))
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
    load_state()
    threading.Thread(target=ticker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print("\n  THE UPGRADE LIVE  —  real-time server running")
    print("  • Audience   http://localhost:%d/" % PORT)
    print("  • Moderator  http://localhost:%d/moderator" % PORT)
    print("  • Projector  http://localhost:%d/projector" % PORT)
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
