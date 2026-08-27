#!/usr/bin/env python3
"""End-to-end smoke test for THE UPGRADE LIVE.

Boots a real server on a spare port against a throwaway data directory, drives
it the way an event would, and asserts what it gets back. Standard library
only, like the app: `python3 test_smoke.py`, exit code 0 means everything held.

It is deliberately black-box — it talks HTTP, never imports the app's internals
— so it keeps working when the code underneath is rearranged. Anything a live
audience could do is exercised as a guest, without the crew cookie, because the
thing worth catching is a crew-only action quietly becoming reachable (or, as
happened once, an audience action quietly becoming crew-only and bouncing every
phone to a passcode screen).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PASSCODE = "smoke-passcode-1234"
ROOM = "WN25"

PASSED = []
FAILED = []


# ---------------------------------------------------------------------------
# tiny test harness
# ---------------------------------------------------------------------------

def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  \033[32m✓\033[0m %s" % name)
    else:
        FAILED.append((name, detail))
        print("  \033[31m✗ %s\033[0m%s" % (name, ("  — " + detail) if detail else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Login answers 303 with the cookie on it; following the redirect would
    throw that header away before we ever saw it."""

    def redirect_request(self, *_args, **_kw):
        return None


class Client:
    """Minimal HTTP client that remembers one cookie."""

    def __init__(self, base):
        self.base = base
        self.cookie = None
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, body=None, ctype="application/json", headers=None):
        data = body
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
        elif isinstance(body, str):
            data = body.encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", ctype)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def json(self, path):
        status, body, _ = self.get(path)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None

    def act(self, kind, **fields):
        fields.setdefault("room", ROOM)
        fields["type"] = kind
        status, body, _ = self.post("/api/action", fields)
        try:
            return status, json.loads(body)
        except ValueError:
            return status, {}


def state(client, room=ROOM):
    _, body = client.json("/api/state?room=" + room)
    return body


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def run(guest, crew, data_dir):
    # ---- the surfaces are all reachable ----
    # "/" answers with a redirect to tonight's room, so the audience page is
    # checked at the address a phone actually lands on
    # the room-taking surfaces are asked for by name; bare ones redirect, which
    # is checked separately below
    for path, label in (("/" + ROOM, "audience"),
                        ("/projector?room=" + ROOM, "projector"),
                        ("/recap?room=" + ROOM, "recap"), ("/setup", "setup"),
                        ("/moderator?room=" + ROOM, "moderator")):
        status, body, _ = guest.get(path)
        check("page serves: %s" % label, status == 200, "got %s" % status)
    status, body, _ = guest.get("/moderator?room=" + ROOM)
    check("crew pages show the lock screen to a guest", b"CREW ONLY" in body)

    # ---- crew sign-in ----
    status, body, headers = crew.post("/login", "passcode=%s&next=/setup" % PASSCODE,
                                      ctype="application/x-www-form-urlencoded")
    cookie = headers.get("Set-Cookie", "")
    crew.cookie = cookie.split(";")[0] if cookie else None
    check("passcode signs the crew in", bool(crew.cookie), "no cookie issued")
    status, _ = crew.json("/api/rooms")
    check("crew can read the room list", status == 200, "got %s" % status)
    # the demo room opens seeded with the mockup's numbers; wipe it so the
    # counts below mean what they say
    crew.post("/api/rooms/%s" % ROOM, {"reset": True})
    status, body, _ = guest.get("/api/rooms")
    check("a guest cannot read the room list", status == 401, "got %s" % status)
    status, body, _ = guest.post("/login", "passcode=wrong-one",
                                 ctype="application/x-www-form-urlencoded")
    check("a wrong passcode is refused", b"CREW ONLY" in body or status >= 400)

    # ---- the room exists and is legible ----
    st = state(guest)
    check("the demo room is open", st.get("exists") is not False)
    check("the room carries its topics", st.get("topicCount", 0) >= 3,
          "topicCount=%s" % st.get("topicCount"))
    st_missing = state(guest, "NOSUCH")
    check("an unopened code is reported, not conjured", st_missing.get("exists") is False)

    # ---- check-in ----
    status, opts = guest.json("/api/onboarding")
    check("check-in options are offered", status == 200
          and len(opts.get("occupations", [])) >= 5
          and len(opts.get("vibes", [])) >= 8,
          "occupations=%s vibes=%s" % (len(opts.get("occupations", [])),
                                       len(opts.get("vibes", []))))
    vibe_ids = [v["id"] for v in opts["vibes"]]
    people = [("p_one", "Founder / Business owner", vibe_ids[0]),
              ("p_two", "Tech / Engineering", vibe_ids[1]),
              ("p_three", "Founder / Business owner", vibe_ids[0])]
    for pid, occ, vibe in people:
        guest.act("join", pid=pid)
        guest.act("profile", pid=pid, name=pid.upper(), occupation=occ,
                  vibe=vibe, checkin=True)
    st = state(guest)
    rs = st.get("roomStats", {})
    check("check-ins are counted", rs.get("checkedIn") == 3, "got %s" % rs.get("checkedIn"))
    top = rs.get("occupations", [{}])[0]
    check("occupations aggregate correctly", top.get("count") == 2,
          "top=%s" % top)
    check("vibes aggregate correctly",
          sum(v["count"] for v in rs.get("vibes", [])) == 3)
    check("room stats carry no names", "P_ONE" not in json.dumps(rs))

    # editing a profile later must not quietly un-check-in the person, or
    # drop the vibe they picked — the sheet doesn't have those fields on it
    guest.act("profile", pid="p_one", name="P One", occupation="Founder / Business owner",
              fact="Likes maps", shared=True, email="one@example.com")
    rs = state(guest)["roomStats"]
    check("editing a profile keeps the check-in", rs["checkedIn"] == 3,
          "checkedIn=%s" % rs["checkedIn"])
    check("editing a profile keeps the vibe",
          sum(v["count"] for v in rs["vibes"]) == 3,
          "vibes=%s" % [v["count"] for v in rs["vibes"]])

    # ---- the privacy notice, and a link to it wherever we ask for something ----
    status, priv, _ = guest.get("/privacy")
    check("the privacy notice is public", status == 200 and b"PRIVACY" in priv)
    for needs in (b"London", b"Australian Privacy Principle 8", b"Spam Act 2003",
                  b"Information Commissioner"):
        check("the notice covers: %s" % needs.decode(), needs in priv)
    _, aud, _ = guest.get("/" + ROOM)
    check("every place we ask for details links to it",
          aud.count(b'href="/privacy"') >= 3,
          "found %d links" % aud.count(b'href="/privacy"'))
    status, meta = guest.json("/api/privacy-meta")
    check("the notice reads its contact address from the mail settings",
          status == 200 and "contact" in meta and "updated" in meta, "got %s" % meta)
    # with no mail configured there is no address to name, and the page must
    # still say something true rather than an empty gap
    check("and falls back to something true when mail isn't set up",
          b"the address on the email you received" in priv)

    # ---- the bundled events are loadable, not just well-formed JSON ----
    # A typo here is only found when you open the room in front of an audience.
    import server as _srv
    for _name in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "events")):
        if not _name.endswith(".json"):
            continue
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "events", _name)) as _fh:
            _cfg = json.load(_fh)
        try:
            _srv.validate_event(_cfg)
            _ok, _why = True, ""
        except Exception as exc:                       # noqa: BLE001 - report it
            _ok, _why = False, str(exc)
        check("bundled event %s is valid (%s)" % (_name, _why or "ok"), _ok)

    # ---- giving is not joining a list ----
    # Tapping the ask used to swap the sheet for "YOU'RE ON THE LIST", which was
    # simply untrue: nobody who donates has joined anything.
    _, aud_js, _ = crew.get("/js/audience.js")
    check("the ask never claims someone joined a list",
          b"!o.opensLink && interested[o.kind]" in aud_js)
    check("the ask is one big button carrying its own words",
          b'o.opensLink ? "btn big" : "btn ghost"' in aud_js)
    check("no email furniture on the ask",
          b'$("#offer-also").hidden = Boolean(o.opensLink);' in aud_js)
    _, app_css, _ = crew.get("/css/app.css")
    check("the big button style exists", b".btn.big{" in app_css)

    # ---- ending the night, and the survey that follows ----
    _, mod_html2, _ = crew.get("/moderator?room=" + ROOM)
    check("the control room has its own end button",
          b'id="endnight-btn"' in mod_html2)
    check("...which did not steal the topbar's id",
          mod_html2.count(b'id="end-btn"') == 1)

    _, st_sv = guest.json("/api/state?room=%s" % ROOM)
    survey = st_sv.get("survey") or {}
    check("the phone is told the survey questions", len(survey.get("questions", {})) == 3)
    check("...and the four things it could have enjoyed",
          survey.get("enjoyed") == ["Discussion", "Interactivity",
                                    "Panelists", "Meeting new people"])

    # the survey only ever happens after the room closes, so a closed room has
    # to keep accepting it — it used to answer 200 and throw the answer away
    crew.request("POST", "/api/rooms/" + ROOM, {"closed": True})
    time.sleep(1.2)
    _, before_arch = crew.json("/api/archive")
    before_n = before_arch.get("responses", 0)
    status, _, _ = guest.post("/api/action", {
        "type": "survey", "room": ROOM, "pid": "smoke-survey",
        "rating": 7, "enjoyed": "Discussion", "next": "More of this"})
    check("a closed room still accepts the survey", status == 200)
    time.sleep(1.4)
    _, after_arch = crew.json("/api/archive")
    check("all three answers are actually written down",
          after_arch.get("responses", 0) - before_n == 3)

    # a rating outside 1-10 and an option nobody offered are dropped, not stored
    guest.post("/api/action", {"type": "survey", "room": ROOM, "pid": "smoke-junk",
                               "rating": 99, "enjoyed": "Free wine", "next": ""})
    time.sleep(1.4)
    _, junk_arch = crew.json("/api/archive")
    check("nonsense answers are not stored",
          junk_arch.get("responses", 0) == after_arch.get("responses", 0))
    crew.request("POST", "/api/rooms/" + ROOM, {"closed": False})

    # ---- the join code can go back on the wall at any point ----
    # People arrive late. The scan-to-join panel used to exist only before the
    # doors opened, so the only way back to it was ending the event.
    crew.post("/api/action", {"type": "showScreen", "which": "join", "room": ROOM})
    _, st_join = guest.json("/api/state?room=%s" % ROOM)
    check("the crew can raise the join code mid-event", st_join.get("screen") == "join")
    crew.post("/api/action", {"type": "showScreen", "which": "holding", "room": ROOM})
    _, st_swap = guest.json("/api/state?room=%s" % ROOM)
    check("raising another screen takes the join code down",
          st_swap.get("screen") == "holding")
    crew.post("/api/action", {"type": "showScreen", "which": "join", "room": ROOM})
    crew.post("/api/action", {"type": "showScreen", "which": "join", "room": ROOM})
    _, st_off = guest.json("/api/state?room=%s" % ROOM)
    check("pressing it again takes it down", st_off.get("screen") is None)
    status, _, _ = guest.post("/api/action",
                              {"type": "showScreen", "which": "join", "room": ROOM})
    check("a guest cannot put the join code up", status == 401)
    _, proj_js, _ = crew.get("/js/projector.js")
    check("the projector shows the join panel when it is asked for",
          b'st.screen !== "join"' in proj_js)
    _, mod_html, _ = crew.get("/moderator?room=" + ROOM)
    check("the control room has a join button", b'id="join-btn"' in mod_html)

    # ---- the three addresses are on the setup page, not just after a click ----
    # They used to be written only by the OPEN ROOM handler, so a reload lost
    # them while the room was still live — and there was no way to reach any
    # other room's projector or phone view at all.
    _, setup_js2, _ = crew.get("/js/setup.js")
    check("the surface links are drawn by a reusable function",
          b"showSurfaceLinks" in setup_js2)
    check("...and are restored when the room list loads, not only on OPEN ROOM",
          setup_js2.count(b"showSurfaceLinks(") >= 3)
    check("every open room row carries its own projector and phone links",
          b"room-surface" in setup_js2 and b"/projector" in setup_js2)

    # ---- the control room is sized, not squashed ----
    # Stopping the page scrolling is easy and worthless on its own: the first
    # attempt left the panels 66px tall holding 296px of questions.
    _, mod_css, _ = crew.get("/css/moderator.css")
    check("the panels have a floor they cannot be crushed below",
          b"min-height:min(244px,29vh)" in mod_css)
    check("the control column scrolls rather than hiding a button",
          b"overflow-y:auto;overflow-x:hidden;" in mod_css)
    check("the vertical rhythm scales with the viewport",
          b"--v:clamp(" in mod_css)

    # ---- picking a reaction ----
    # A room needs somewhere to put boredom and disgust, not just applause, and
    # typing an emoji on a laptop is why every event ended up with the same four.
    _, setup_js, _ = crew.get("/js/setup.js")
    check("setup offers a palette of reactions rather than a text box",
          b"EMOJI_PALETTE" in setup_js and b"emojiPicker" in setup_js)
    for feeling in ("\U0001F4A9", "\U0001F44E", "\U0001F971", "\U0001F644"):
        check("the palette includes %s" % feeling,
              feeling.encode("utf-8") in setup_js)

    # ---- the control room fits the screen it is driven on ----
    _, mod_css2, _ = guest.get("/css/moderator.css")
    check("the control room is sized to the viewport, not the content",
          b"height:100dvh" in mod_css2,
          "without this the page scrolls and buttons hide below the fold")
    check("and the reading panes scroll inside themselves",
          b"overflow-y:auto" in mod_css2.split(b"\n.panel{")[1][:200],
          "a panel that cannot scroll just hides what is past its edge")

    # ---- readable in a dark room, and usable without sight ----
    # Measured rather than eyeballed: --dim carried real text at 2.76:1, which
    # is unreadable on a phone at arm's length in a venue.
    _, app_css, _ = guest.get("/css/app.css")
    import re as _c
    tokens = dict(_c.findall(rb"--([\w-]+):\s*(#[0-9a-fA-F]{6})", app_css))

    def _lum(h):
        h = h.decode().lstrip("#")
        vals = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
        return .2126 * f(vals[0]) + .7152 * f(vals[1]) + .0722 * f(vals[2])

    bg = tokens.get(b"bg", b"#0a0a0b")
    for name in (b"ink", b"muted", b"dim"):
        if name not in tokens:
            continue
        la, lb = _lum(tokens[name]), _lum(bg)
        contrast = (max(la, lb) + .05) / (min(la, lb) + .05)
        check("text colour --%s is readable (WCAG AA)" % name.decode(),
              contrast >= 4.5, "%.2f:1, needs 4.5" % contrast)

    # Structure, not just text: at 7% white on near-black, nothing had a visible
    # edge and the whole interface floated in one shade of dark.
    edges = dict(_c.findall(rb"--(line-?2?):\s*rgba\(255,255,255,\.(\d+)\)", app_css))
    for token, floor in ((b"line", 12), (b"line-2", 20)):
        got = int(edges.get(token, b"0"))
        check("--%s is strong enough to see an edge" % token.decode(),
              got >= floor, "at .%02d, wants at least .%d" % (got, floor))

    _, aud_html, _ = guest.get("/" + ROOM)
    fields = _c.findall(rb"<(?:input|textarea|select)[^>]*>", aud_html)
    # A visible <label for=…> is a real label — better than aria-label, since a
    # sighted user reads it too. Accept either, and nothing else.
    labelled_ids = set(_c.findall(rb'<label[^>]*\bfor="([^"]+)"', aud_html))
    def _named(f):
        if b"aria-label" in f:
            return True
        fid = _c.search(rb'\bid="([^"]+)"', f)
        return bool(fid and fid.group(1) in labelled_ids)
    bare = [f for f in fields if not _named(f) and b"hidden" not in f]
    check("every field a guest types into is labelled for a screen reader",
          not bare, "%d unlabelled: %s" % (len(bare), b" ".join(bare)[:120]))

    for sheet in ("audience", "projector", "moderator"):
        _, sheet_css, _ = guest.get("/css/%s.css" % sheet)
        tiny = [t for t in _c.findall(rb"font-size:([0-9.]+)px", sheet_css)
                if float(t) < 10]
        check("%s.css has no text below 10px" % sheet, not tiny,
              "found: %s" % sorted({t.decode() for t in tiny}))

    # ---- the client must not read snapshot fields the server never sends ----
    # This is here because renaming three snapshot fields into one silently
    # disabled the offer sheet on every phone: the JS kept testing the old name,
    # which was simply undefined, so the condition was permanently false. No
    # error, no failing test, just a feature that quietly stopped happening.
    snap = state(guest)
    known = set(snap) | {"promo", "promos", "screen", "started", "runningOrder"}
    import re as _re
    for name in ("audience", "projector", "moderator"):
        _, js, _ = guest.get("/js/%s.js" % name)
        # comments are free to name a field they are explaining
        code = _re.sub(rb"/\*.*?\*/", b"", js, flags=_re.S)
        code = _re.sub(rb"(?m)//.*$", b"", code)
        used = set(_re.findall(rb"\bst\.([A-Za-z_][A-Za-z0-9_]*)", code))
        unknown = [u.decode() for u in sorted(used) if u.decode() not in known]
        check("%s.js reads only fields the server sends" % name, not unknown,
              "unknown: %s" % ", ".join(unknown))

    # ---- the control room says what the audience is actually looking at ----
    _, mod_js, _ = guest.get("/js/moderator.js")
    check("the mode label reflects a takeover, not just the moderator's intent",
          b"ON THE WALL" in mod_js,
          "saying DISCUSSION while the holding loop is up describes intent, not the room")
    _, mod_html, _ = crew.get("/moderator?room=" + ROOM)
    import re as _m
    rows = _m.findall(rb'<div class="qa-row">(.*?)</div>\s*\n', mod_html, _m.S)
    check("the wall controls and the show controls are separate rows",
          len(rows) >= 2, "found %d rows" % len(rows))
    check("both rows are labelled",
          b">On the wall<" in mod_html and b">Run the show<" in mod_html)
    _, mod_css, _ = guest.get("/css/moderator.css")
    check("the panels share one row by weight, so the wordy ones get the width",
          b"repeat(12,1fr)" in mod_css.split(b".panels{")[1][:140],
          "equal columns gave every pane 167px, too narrow to read a sentence in")
    tiny_labels = [t for t in _c.findall(rb"font-size:([0-9.]+)px", mod_css)
                   if float(t) < 12]
    check("nothing in the control room is under 12px any more",
          not tiny_labels, "found: %s" % sorted({t.decode() for t in tiny_labels}))

    # ---- a challenge reaches the wall only when the crew puts it there ----
    # The pulse announces that somebody pushed back; the words are 180
    # characters nobody has read, so they wait for a decision.
    guest.act("challenge", pid="p_push", name="Pusher", text="I think that's wrong.")
    st = state(crew)
    cid = next(c["id"] for c in st["challenges"] if c["name"] == "Pusher")
    check("a new challenge is not on the wall by itself",
          st.get("featuredChallenge") is None, "got %s" % st.get("featuredChallenge"))
    status, _ = guest.act("featureChallenge", id=cid)
    check("a guest cannot put words on the projector", status == 401, "got %s" % status)

    crew.act("showScreen", which="holding", on=True)
    crew.act("featureChallenge", id=cid)
    st = state(guest)
    check("the crew can put a challenge up", st["featuredChallenge"] == cid)
    check("and it clears whatever takeover was covering the wall",
          st["screen"] is None, "screen=%s" % st["screen"])

    # one thing on the wall at a time, whichever kind it is
    guest.act("ask", pid="p_push", text="And what about the other side?")
    other = next(q["id"] for q in state(guest)["questions"]
                 if "other side" in q["text"])
    crew.act("featureQuestion", id=other)
    check("featuring a question takes the challenge down — one thing at a time",
          state(guest)["featuredChallenge"] is None)
    crew.act("featureQuestion", id=other)

    crew.act("featureChallenge", id=cid)
    crew.act("removeChallenge", id=cid)
    check("removing a challenge takes it off the wall too",
          state(guest)["featuredChallenge"] is None)
    crew.act("undoRemove")

    # ---- undo: a mis-tap in front of a room must not be final ----
    guest.act("ask", pid="p_undo", text="Can this be taken back?")
    st = state(guest)
    target = next(q["id"] for q in st["questions"] if "taken back" in q["text"])
    status, res = crew.act("removeQuestion", id=target)
    check("removing a question offers an undo", res.get("undo") == "question",
          "got %s" % res)
    gone = state(crew)
    check("and it is gone in the meantime",
          not any(q["id"] == target for q in gone["questions"]))
    check("the crew is told an undo is available", gone.get("undoable") is True)
    check("a guest is never told that", state(guest).get("undoable") is not True)
    status, _ = guest.act("undoRemove")
    check("and a guest cannot undo the crew's removal", status == 401,
          "got %s" % status)
    crew.act("undoRemove")
    back = state(crew)
    check("undo puts the question back",
          any(q["id"] == target for q in back["questions"]))
    check("and the offer goes away once used", back.get("undoable") is False)
    crew.act("removeQuestion", id=target)

    # ---- removing a stale room, without destroying what happened in it ----
    crew.post("/api/rooms", {"code": "STALE", "eventId": "demo_event"})
    guest.post("/api/leads", {"room": "STALE", "email": "held@example.com"})
    status, _, _ = guest.request("DELETE", "/api/rooms/STALE")
    check("a guest cannot delete a room", status == 401, "got %s" % status)
    status, body, _ = crew.request("DELETE", "/api/rooms/STALE")
    check("the crew can delete a room", status == 200, "got %s" % status)
    _, rooms_left = crew.json("/api/rooms")
    check("and it leaves the list",
          not any(r["code"] == "STALE" for r in rooms_left["rooms"]))
    status, held = crew.json("/api/leads/STALE.json")
    check("the sign-ups it held are not deleted with it",
          status == 200 and any(l["email"] == "held@example.com" for l in held),
          "got %s" % held)
    status, _, _ = crew.request("DELETE", "/api/rooms/STALE")
    check("deleting it again says so rather than pretending", status == 404,
          "got %s" % status)

    # ---- the lobby ----
    # A room that hasn't been started keeps everyone in the lobby, and the wall
    # off the first debate question — nobody should read it before it is asked.
    st = state(guest)
    check("a fresh room has not started", st["started"] is False)
    check("the lobby knows the running order",
          len(st.get("runningOrder", [])) == st["topicCount"],
          "%s vs %s" % (len(st.get("runningOrder", [])), st["topicCount"]))
    status, _ = guest.act("startEvent")
    check("a guest cannot start the event", status == 401, "got %s" % status)
    crew.act("startEvent")
    st = state(guest)
    check("the crew can start it", st["started"] is True)
    check("and the night begins on the holding loop, not on topic one",
          st["screen"] == "holding", "screen=%s" % st["screen"])
    # and if the crew forgets the button and simply drives the show, the room
    # must not be left sitting in the lobby
    crew.post("/api/rooms/%s" % ROOM, {"reset": True})
    check("a reset puts the room back in the lobby", state(guest)["started"] is False)
    crew.act("nextTopic")
    check("driving the show starts it anyway", state(guest)["started"] is True)
    crew.act("prevTopic")
    # the reset above emptied the room; put it back so the rest of the run has
    # the people it expects
    for pid, occ, vibe in people:
        guest.act("join", pid=pid)
        guest.act("profile", pid=pid, name=pid.upper(), occupation=occ,
                  vibe=vibe, checkin=True)

    # ---- every audience action a phone can send ----
    guest.act("sentiment", pid="p_one", choice="agree")
    guest.act("sentiment", pid="p_two", choice="disagree")
    st = state(guest)
    check("sentiment is tallied",
          st["sentiment"]["agree"] >= 1 and st["sentiment"]["disagree"] >= 1)
    guest.act("sentiment", pid="p_one", choice="disagree")
    st = state(guest)
    check("changing your mind moves the vote rather than adding one",
          st["sentiment"]["agree"] == 0, "agree=%s" % st["sentiment"]["agree"])

    guest.act("whatsnext", pid="p_one")
    check("what's next is counted", state(guest)["whatsNext"]["votes"] >= 1)

    guest.act("challenge", pid="p_two", name="Two", text="I'd push back on that")
    check("a challenge joins the queue",
          any("push back" in c["text"] for c in state(guest)["challenges"]))

    _, res = guest.act("ask", pid="p_two", text="How does this scale?")
    st = state(guest)
    qid = st["questions"][0]["id"] if st["questions"] else None
    check("a question reaches the floor", qid is not None)
    start = state(guest)["questions"][0]["votes"]
    check("asking counts as the asker's own vote", start == 1, "votes=%s" % start)
    guest.act("upvote", pid="p_one", id=qid)
    check("questions can be upvoted", state(guest)["questions"][0]["votes"] == start + 1)
    guest.act("upvote", pid="p_one", id=qid)
    check("tapping again takes the upvote back",
          state(guest)["questions"][0]["votes"] == start)

    status, _ = guest.act("burst", pid="p_one", emoji="🔥")
    check("reaction bursts are accepted", status == 200)

    # ---- interactions, driven by the crew ----
    crew.act("launchInteraction", index=0)
    st = state(guest)
    check("the crew can launch a poll", st["mode"] == "poll", "mode=%s" % st["mode"])
    option = st["poll"]["options"][0]["id"]
    guest.act("poll", pid="p_one", option=option)
    guest.act("poll", pid="p_two", option=option)
    guest.act("poll", pid="p_two", option=option)
    crew.act("reveal")
    votes = {o["id"]: o["votes"] for o in state(guest)["poll"]["options"]}
    check("one phone gets one poll vote", votes[option] >= 2)
    before = votes[option]
    guest.act("poll", pid="p_two", option=st["poll"]["options"][1]["id"])
    after = {o["id"]: o["votes"] for o in state(guest)["poll"]["options"]}
    check("changing a poll vote moves it", after[option] == before - 1)

    # results stay hidden until the crew reveals them
    crew.act("launchInteraction", index=0)
    hidden = state(guest)
    check("a fresh poll hides its numbers from the room",
          all(o.get("votes") in (None, 0) for o in hidden["poll"]["options"]),
          "guest could see %s" % hidden["poll"]["options"])

    crew.act("launchInteraction", index=1)
    check("the crew can launch a word cloud", state(guest)["mode"] == "wordcloud")

    # ---- word cloud moderation ----
    guest.act("word", pid="p_clean", word="hopeful")
    words = {w["text"] for w in state(guest)["wordcloud"]["words"]}
    check("a clean word lands", "hopeful" in words)

    _, r1 = guest.act("word", pid="p_troll", word="fuck")
    _, r2 = guest.act("word", pid="p_troll", word="sh1t")
    words = {w["text"] for w in state(guest)["wordcloud"]["words"]}
    check("profanity never reaches the wall", not ({"fuck", "sh1t"} & words))
    check("a filtered word is answered exactly like one that landed",
          r1.get("wordsLeft") == 2 and r2.get("wordsLeft") == 1,
          "%s / %s" % (r1, r2))

    for n in range(5):
        guest.act("word", pid="p_spam", word="spamword%d" % n)
    landed = [w for w in state(guest)["wordcloud"]["words"] if w["text"].startswith("spamword")]
    check("one phone is capped at three words", len(landed) == 3, "landed %d" % len(landed))

    crew.act("removeWord", word="hopeful")
    words = {w["text"] for w in state(guest)["wordcloud"]["words"]}
    check("the crew can pull a word off the wall", "hopeful" not in words)
    guest.act("word", pid="p_other", word="hopeful")
    words = {w["text"] for w in state(guest)["wordcloud"]["words"]}
    check("a removed word cannot be put back", "hopeful" not in words)
    status, _ = guest.act("removeWord", word="uncertain")
    check("a guest cannot remove words", status == 401, "got %s" % status)

    # ---- the remaining interaction kinds ----
    modes = {i["kind"]: i["index"] for i in state(guest)["interactions"]}
    if "emoji" in modes:
        crew.act("launchInteraction", index=modes["emoji"])
        st = state(guest)
        rid = st["emoji"]["reactions"][0]["id"]
        guest.act("emoji", pid="p_one", id=rid)
        check("emoji reactions are tallied",
              any(r["count"] > 0 for r in state(guest)["emoji"]["reactions"]))
    crew.act("nextTopic")
    modes = {i["kind"]: i["index"] for i in state(guest)["interactions"]}
    if "slider" in modes:
        crew.act("launchInteraction", index=modes["slider"])
        before = state(guest)["slider"]["count"]
        guest.act("slider", pid="p_one", value=80)
        crew.act("reveal")
        after = state(guest)["slider"]
        check("the slider takes a value",
              after["count"] == before + 1 and after["avg"] > 0, "got %s" % after)
    crew.act("nextTopic")
    modes = {i["kind"]: i["index"] for i in state(guest)["interactions"]}
    if "ranking" in modes:
        crew.act("launchInteraction", index=modes["ranking"])
        st = state(guest)
        order = [i["id"] for i in st["ranking"]["items"]]
        guest.act("ranking", pid="p_one", order=order)
        crew.act("reveal")
        ranked = state(guest)["ranking"]
        check("a ranking submission is scored",
              any(i.get("score", 0) > 0 for i in ranked["items"]), "got %s" % ranked)

    # ---- every action a phone sends must be accepted without a passcode ----
    # The inverse of the crew gate below, and the one that actually bit: `join`
    # once slipped out of the audience list and every phone that scanned the QR
    # was bounced to the crew passcode screen. Nothing else here noticed,
    # because a silent 401 on join breaks no tally.
    # walk back to a topic that has a poll — the run above has moved on
    for _ in range(5):
        if "poll" in {i["kind"] for i in state(guest)["interactions"]}:
            break
        crew.act("prevTopic")
    kinds = {i["kind"]: i["index"] for i in state(guest)["interactions"]}
    crew.act("launchInteraction", index=kinds["poll"])
    st = state(guest)
    poll_option = st["poll"]["options"][0]["id"]
    guest_actions = [
        ("join", {}),
        ("sentiment", {"choice": "agree"}),
        ("whatsnext", {}),
        ("challenge", {"name": "G", "text": "a point"}),
        ("poll", {"option": poll_option}),
        ("ask", {"text": "a question"}),
        ("burst", {"emoji": "🔥"}),
        ("profile", {"name": "G", "occupation": "Student", "vibe": vibe_ids[0]}),
        ("interested", {"email": "g@example.com"}),
        ("connect", {"to": "p_two"}),
        ("connectRespond", {"from": "p_two", "accept": False}),
        ("forgetMe", {}),
    ]
    for kind, fields in guest_actions:
        status, _ = guest.act(kind, pid="p_guestcheck", **fields)
        check("a phone may send: %s" % kind, status == 200, "got %s" % status)
    if "wordcloud" in kinds:
        crew.act("launchInteraction", index=kinds["wordcloud"])
    status, _ = guest.act("word", pid="p_guestcheck", word="fine")
    check("a phone may send: word", status == 200, "got %s" % status)

    # ---- crew-only actions are all closed to guests ----
    for kind in ("launchInteraction", "nextTopic", "prevTopic", "reveal", "askAgain",
                 "extend", "pause", "showResults", "backToDiscussion", "removeQuestion",
                 "removeChallenge", "featureQuestion", "featureProfile", "inviteTop",
                 "showScreen", "startEvent", "removeWord", "reset"):
        status, _ = guest.act(kind, index=0)
        check("guests are refused: %s" % kind, status == 401, "got %s" % status)

    # ---- getting in, with the least possible friction ----
    st = state(guest)
    check("the join URL is a sayable path, not a query string",
          st["joinUrl"].rstrip("/").endswith("/" + ROOM), "got %s" % st["joinUrl"])
    for path in ("/" + ROOM, "/" + ROOM.lower()):
        status, body, _ = guest.get(path)
        check("a bare room code serves the audience page: %s" % path,
              status == 200 and b"WHAT'S" in body, "got %s" % status)
    # A named page must never be mistaken for a room. Matched on the <title>,
    # which is unique per page — an earlier version of this test matched on
    # body text that the audience page also contains, so it passed happily
    # while the routing was broken.
    for path, title in (("/recap?room=" + ROOM, b"The Debrief"),
                        ("/projector?room=" + ROOM, b"Room Display"),
                        ("/print?room=" + ROOM, b"Crew"),   # lock screen for a guest
                        ("/setup", b"Crew"), ("/crm", b"Crew")):
        status, body, _ = guest.get(path)
        head = body.split(b"</title>")[0]
        check("a named page still wins over a room code: %s" % path,
              status == 200 and title in head, "title was %s" % head[-60:])
    status, body, _ = guest.get("/print?room=" + ROOM)
    check("the print sheet shows a guest the lock screen, like the other crew pages",
          status == 200 and b"CREW ONLY" in body and b"PRINT THE JOIN CODE" not in body,
          "got %s" % status)
    status, body, _ = crew.get("/print?room=" + ROOM)
    check("the crew can open the print sheet",
          status == 200 and b"PRINT THE JOIN CODE" in body, "got %s" % status)
    # the audience page must be the one that answers a room path, or the QR
    # leads somewhere useless
    status, body, _ = guest.get("/" + ROOM)
    check("a scanned code lands on the audience page, not a redirect",
          b"JOIN THE DEBATE" in body and b"s-checkin" in body)

    # The bare address must find tonight's room. Sending it to a hardcoded demo
    # room is the worst case of all: the demo is always open and seeded, so
    # someone would believe they had joined and vote where nobody is reading.
    status, _, headers = guest.get("/")
    check("the bare address goes to a room, not a guess",
          status in (301, 302, 303) and headers.get("Location", "").lstrip("/") == ROOM,
          "%s -> %s" % (status, headers.get("Location")))

    crew.post("/api/rooms", {"code": "LATER", "eventId": "demo_event"})
    status, _, headers = guest.get("/")
    check("and follows the room opened most recently",
          headers.get("Location", "").lstrip("/") == "LATER",
          "went to %s" % headers.get("Location"))
    crew.post("/api/rooms/LATER", {"closed": True})
    status, _, headers = guest.get("/")
    check("switching that room off hands it back",
          headers.get("Location", "").lstrip("/") == ROOM,
          "went to %s" % headers.get("Location"))

    # Every surface follows tonight's room, so nothing depends on the demo room
    # still existing and a bookmark survives the code changing.
    for surface in ("/projector", "/recap"):
        status, _, headers = guest.get(surface)
        check("%s finds tonight's room on its own" % surface,
              headers.get("Location", "").endswith("room=" + ROOM),
              "went to %s" % headers.get("Location"))
    for surface in ("/moderator", "/print"):
        status, _, headers = crew.get(surface)
        check("%s finds tonight's room on its own" % surface,
              headers.get("Location", "").endswith("room=" + ROOM),
              "went to %s" % headers.get("Location"))
    status, _, headers = crew.get("/moderator?room=" + ROOM)
    check("and a named room is never redirected away",
          status == 200 and "Location" not in headers, "got %s" % status)

    # an explicit ?room= is a promise; the redirect must not break older links
    status, body, headers = guest.get("/?room=" + ROOM)
    check("an explicit ?room= is never redirected away",
          status == 200 and "Location" not in headers, "got %s" % status)

    # ---- the projector's holding loop ----
    status, body, headers = guest.get("/media/holding.mp4")
    check("the holding video is served", status == 200 and len(body) > 1000,
          "status=%s len=%s" % (status, len(body)))
    check("it is served as video", headers.get("Content-Type") == "video/mp4",
          headers.get("Content-Type"))
    status, body, headers = guest.request("GET", "/media/holding.mp4",
                                          headers={"Range": "bytes=0-99"})
    check("byte ranges are answered — Safari will not play video without them",
          status == 206 and len(body) == 100 and "Content-Range" in headers,
          "status=%s len=%s" % (status, len(body)))
    crew.act("showScreen", which="holding", on=True)
    check("the crew can raise the holding screen", state(guest)["screen"] == "holding")
    crew.act("showScreen", which="holding", on=False)
    check("and take it down", state(guest)["screen"] is None)

    # ---- state of the room on the wall ----
    crew.act("showScreen", which="explainer", on=True)
    check("the crew can put the explainer up", state(guest)["screen"] == "explainer")
    # the explainer is what a room watches before anything else happens, so the
    # slides and their demos have to exist rather than silently render nothing
    _, proj_js, _ = guest.get("/js/projector.js")
    for demo in (b"join", b"stand", b"vote", b"ask", b"mic", b"react", b"mail"):
        check("the explainer has a demo for: %s" % demo.decode(),
              b'demo: "' + demo + b'"' in proj_js)
    check("each slide is given time to be watched, not just read",
          b"HOW_DWELL = 10000" in proj_js,
          "dwell is not 10s — a shortened one may have been left in from testing")
    crew.act("showScreen", which="stats", on=True)
    check("the crew can put the room's make-up on the wall",
          state(guest)["screen"] == "stats")
    crew.act("showScreen", which="stats", on=False)

    # ---- the offer ----
    status, ev = crew.json("/api/events/demo_event")
    config = ev["config"]
    config["offer"] = {"headline": "Work with me", "body": "Twelve weeks.",
                       "cta": "I'M INTERESTED", "link": "example.com/coaching",
                       "linkLabel": "Details", "image": ""}
    config["donate"] = {"headline": "Keep these nights free", "body": "Anything helps.",
                        "cta": "I'D LIKE TO GIVE", "link": "example.com/give",
                        "linkLabel": "Give here", "image": ""}
    status, body, _ = crew.post("/api/events/demo_event", config)
    check("the offer can be set up", status == 200, body[:120].decode("utf-8", "replace"))
    st = state(guest)
    promos = st.get("promos") or {}
    check("the offer reaches a room that is already open",
          (promos.get("offer") or {}).get("headline") == "Work with me")
    check("the donate ask does too",
          (promos.get("donate") or {}).get("headline") == "Keep these nights free")
    check("promo links are normalised to real URLs",
          all(p.get("link", "").startswith("http") for p in promos.values()))
    check("donate opens its link, the offer doesn't",
          promos["donate"].get("opensLink") is True
          and not promos["offer"].get("opensLink"))

    # The image is shown as it is, not forced into a shape. A square was the
    # better fit on the projector — where the column is as tall as it is wide —
    # but the phone was cropping 44% off one.
    _, aud_css, _ = guest.get("/css/audience.css")
    hero = aud_css.split(b".offer-hero{")[1][:180]
    check("the phone doesn't force the offer image into an aspect ratio",
          b"aspect-ratio" not in hero, "hero rule: %s" % hero[:90])
    check("and doesn't crop it", b"object-fit:contain" in hero,
          "hero rule: %s" % hero[:90])

    # The ask is one button that travels; the offer is two, because reading more
    # and leaving an address are different intentions.
    _, aud_html2, _ = guest.get("/" + ROOM)
    check("the offer sheet's link is a button, not a footnote",
          b'<a class="btn" id="offer-link"' in aud_html2,
          "the link is still styled as small print")
    _, aud_js, _ = guest.get("/js/audience.js")
    check("the ask hides the email button, leaving only the link",
          b'emailBtn.hidden = Boolean(o.opensLink)' in aud_js)
    check("and a tap on the ask's link is still recorded",
          b"noteInterest" in aud_js)

    crew.act("showScreen", which="offer", on=True)
    check("the crew can put the offer up", state(guest)["screen"] == "offer")
    # One projector, one screen. Every takeover shares the field, so no two can
    # ever both be up — including the holding loop and the room breakdown.
    for which in ("donate", "holding", "explainer", "stats", "offer"):
        crew.act("showScreen", which=which, on=True)
        st = state(guest)
        check("putting up %s takes down whatever was there" % which,
              st["screen"] == which, "got %s" % st["screen"])
    crew.act("showScreen", which="offer", on=False)
    check("and it can be taken down", state(guest)["screen"] is None)
    # putting a question on the wall has to clear the takeover, or it lands
    # behind whatever is already there and appears to do nothing
    crew.act("showScreen", which="offer", on=True)
    crew.act("featureQuestion", id=qid)
    check("featuring a question clears the takeover", state(guest)["screen"] is None,
          "got %s" % state(guest)["screen"])
    crew.act("showScreen", which="offer", on=True)
    _, before_rows = crew.json("/api/interest/%s.json" % ROOM)
    guest.act("interested", pid="p_one", name="One", email="one@example.com")
    guest.act("interested", pid="p_one", name="One", email="one@example.com")
    # the same phone can answer both, and they are counted separately
    crew.act("showScreen", which="donate", on=True)
    guest.act("interested", pid="p_one", name="One", email="one@example.com")
    crew.act("showScreen", which="offer", on=True)
    status, rows = crew.json("/api/interest/%s.json" % ROOM)
    check("raising a hand is recorded once per promo, however often it is tapped",
          status == 200 and len(rows) == len(before_rows) + 2,
          "before=%s after=%s" % (len(before_rows), len(rows)))
    check("the two are told apart in the record",
          {r.get("promo") for r in rows} >= {"offer", "donate"},
          "got %s" % [r.get("promo") for r in rows])
    interest_count = len(rows)
    status, _, _ = guest.get("/api/interest/%s.json" % ROOM)
    check("the interest list is crew-only", status == 401, "got %s" % status)

    # ---- debrief sign-ups ----
    status, body, _ = guest.post("/api/leads", {"room": ROOM, "email": "sign@example.com",
                                                "name": "Signer"})
    check("a guest can sign up for the debrief without a passcode", status == 200,
          "got %s" % status)
    status, body, _ = guest.post("/api/leads", {"room": ROOM, "email": "not-an-email"})
    check("a bad address is refused", status == 400, "got %s" % status)

    # An address given to the offer is an address given at this event: it has to
    # reach the debrief list too, or the person is stranded on a list that never
    # gets sent and the crew never knows they are owed anything.
    _, before_leads = crew.json("/api/leads/%s.json" % ROOM)
    crew.act("showScreen", which="offer", on=True)
    guest.act("interested", pid="p_stranded", name="Stranded",
              email="stranded@example.com")
    _, after_leads = crew.json("/api/leads/%s.json" % ROOM)
    check("an address given to the offer joins the debrief list",
          any(l["email"] == "stranded@example.com" for l in after_leads)
          and len(after_leads) == len(before_leads) + 1,
          "%d then %d" % (len(before_leads), len(after_leads)))
    _, interest_rows = crew.json("/api/interest/%s.json" % ROOM)
    check("and still counts separately as an offer hand-raise",
          any(i["email"] == "stranded@example.com" and i.get("promo") == "offer"
              for i in interest_rows))
    guest.act("interested", pid="p_stranded", name="Stranded",
              email="stranded@example.com")
    _, again = crew.json("/api/leads/%s.json" % ROOM)
    check("tapping it twice doesn't double them up",
          len(again) == len(after_leads), "%d then %d" % (len(after_leads), len(again)))
    status, rooms_now = crew.json("/api/rooms")
    check("setup is told whether mail can send, without needing a sign-up first",
          "mailConfigured" in rooms_now, "keys: %s" % list(rooms_now))

    # the debrief email, viewable without needing mail to work first
    status, _, _ = guest.get("/api/debrief-preview/%s" % ROOM)
    check("the email preview is crew-only", status == 401, "got %s" % status)
    status, body, _ = crew.get("/api/debrief-preview/%s" % ROOM)
    check("the crew can read the exact email before sending it",
          status == 200 and b"THE DEBRIEF" in body and b"Unsubscribe" in body,
          "got %s" % status)
    status, leads = crew.json("/api/leads/%s.json" % ROOM)
    check("sign-ups are readable by the crew", status == 200 and len(leads) >= 1,
          "got %s rows" % (len(leads) if leads is not None else None))
    status, _, _ = guest.get("/api/leads/%s.json" % ROOM)
    check("but not by anyone else", status == 401, "got %s" % status)

    # ---- a full or unmounted volume must never be reported as success ----
    # This one shipped: a failed write was indistinguishable from "already on
    # the list", so a guest whose address was never saved got a tick and a
    # promise of a debrief that could not arrive.
    if os.geteuid() != 0:
        leads_dir = os.path.join(data_dir, "leads")
        os.makedirs(leads_dir, exist_ok=True)
        mode = os.stat(leads_dir).st_mode
        os.chmod(leads_dir, 0o500)
        try:
            status, body, _ = guest.post("/api/leads", {"room": ROOM,
                                                        "email": "unsaveable@example.com"})
            check("a sign-up that cannot be written is reported as a failure",
                  status != 200, "got %s: %s" % (status, body[:80]))
            status, body, _ = guest.post("/api/leads", {"room": ROOM,
                                                        "email": "sign@example.com"})
            check("someone already on the list still gets a yes",
                  status == 200, "got %s" % status)
        finally:
            os.chmod(leads_dir, mode)
        status, body, _ = guest.post("/api/leads", {"room": ROOM,
                                                    "email": "unsaveable@example.com"})
        check("and it works again once the volume is writable", status == 200,
              "got %s" % status)

    # ---- unsubscribe and deletion ----
    status, body, _ = guest.get("/unsubscribe?e=sign%40example.com&t=forged")
    check("a forged unsubscribe link is refused", status == 400, "got %s" % status)

    # ---- reset keeps the things that are not rehearsal data ----
    lead_count = len(crew.json("/api/leads/%s.json" % ROOM)[1])
    interest_count = len(crew.json("/api/interest/%s.json" % ROOM)[1])
    crew.post("/api/rooms/%s" % ROOM, {"reset": True})
    st = state(guest)
    check("reset clears the room", st["sentiment"]["agree"] == 0
          and st["whatsNext"]["votes"] == 0 and not st["challenges"])
    status, rows = crew.json("/api/interest/%s.json" % ROOM)
    check("reset does not destroy real leads", len(rows) == interest_count,
          "%d before, %d after" % (interest_count, len(rows)))
    status, leads = crew.json("/api/leads/%s.json" % ROOM)
    check("reset does not destroy debrief sign-ups", len(leads) == lead_count,
          "%d before, %d after" % (lead_count, len(leads)))

    # ---- deleting yourself ----
    guest.act("join", pid="p_gone")
    guest.act("profile", pid="p_gone", name="Gone", occupation="Student",
              vibe=vibe_ids[0], checkin=True)
    guest.act("ask", pid="p_gone", text="Will this be deleted?")
    before = state(guest)["roomStats"]["checkedIn"]
    guest.act("forgetMe", pid="p_gone")
    st = state(guest)
    check("a person can delete themselves from the room",
          st["roomStats"]["checkedIn"] == before - 1
          and not any("deleted" in q["text"] for q in st["questions"]))

    # ---- the archive ----
    status, _, _ = guest.get("/api/archive")
    check("the archive summary is crew-only", status == 401, "got %s" % status)
    status, arc = crew.json("/api/archive")
    check("the archive is recording responses", status == 200 and arc["responses"] > 0,
          "got %s" % arc)
    check("the archive is recording who came", arc["attendees"] >= 3,
          "attendees=%s" % arc.get("attendees"))
    check("sign-ups are recorded as contacts", arc["contacts"] >= 1,
          "contacts=%s" % arc.get("contacts"))

    # a RESET wipes the room but must not wipe the record — this is what the
    # archive exists for, and what used to destroy an evening
    before = crew.json("/api/archive")[1]
    crew.post("/api/rooms/%s" % ROOM, {"reset": True})
    after = crew.json("/api/archive")[1]
    check("a reset keeps everyone who attended",
          after["attendees"] == before["attendees"],
          "%s then %s" % (before["attendees"], after["attendees"]))
    check("a reset keeps every response recorded",
          after["responses"] == before["responses"],
          "%s then %s" % (before["responses"], after["responses"]))
    check("a reset marks that session as a rehearsal",
          after["rehearsals"] > before["rehearsals"],
          "%s then %s" % (before["rehearsals"], after["rehearsals"]))

    # and deleting yourself has to reach the archive too, or the promise is
    # only half kept
    guest.act("join", pid="p_archgone")
    guest.act("profile", pid="p_archgone", name="Gone", occupation="Student",
              vibe=vibe_ids[0], checkin=True)
    guest.act("sentiment", pid="p_archgone", choice="agree")
    mid = crew.json("/api/archive")[1]
    guest.act("forgetMe", pid="p_archgone")
    end = crew.json("/api/archive")[1]
    check("deleting yourself removes you from the archive",
          end["attendees"] == mid["attendees"] - 1,
          "%s then %s" % (mid["attendees"], end["attendees"]))
    check("and takes your responses with you",
          end["responses"] < mid["responses"],
          "%s then %s" % (mid["responses"], end["responses"]))

    # ---- the CRM ----
    for route in ("overview", "people", "person?id=1", "session?id=1", "people.csv"):
        status, _, _ = guest.get("/api/crm/" + route)
        check("the CRM is crew-only: %s" % route.split("?")[0], status == 401,
              "got %s" % status)

    status, over = crew.json("/api/crm/overview")
    check("the CRM overview reads", status == 200 and over["summary"]["attendees"] > 0)
    check("it lists the events", len(over["sessions"]) >= 1)

    # Somebody who attends and then identifies themselves. It has to happen
    # here: the reset tested above marked the earlier session as a rehearsal,
    # and rehearsals are correctly left out of these counts.
    guest.act("join", pid="p_crm")
    guest.act("profile", pid="p_crm", name="Crm Tester",
              occupation="Founder / Business owner", vibe=vibe_ids[0], checkin=True)
    guest.act("sentiment", pid="p_crm", choice="agree")
    # and then changes their mind, twice — the archive keeps all three
    # positions, but a report that counted them all would say three people
    # were in a room that held one
    guest.act("sentiment", pid="p_crm", choice="disagree")
    guest.act("sentiment", pid="p_crm", choice="unsure")
    guest.act("ask", pid="p_crm", text="Is any of this written down?")
    guest.post("/api/leads", {"room": ROOM, "email": "crm@example.com",
                              "name": "Crm Tester", "pid": "p_crm"})

    status, folk = crew.json("/api/crm/people?sort=nights")
    check("people appear once they give an email", folk["total"] >= 1,
          "total=%s" % folk["total"])
    # sorted by attendance: an address can exist with no nights (given at a
    # sign-up whose phone never checked in), which is fine but not the case
    # this is testing
    who = next((p for p in folk["people"] if p["email"] == "crm@example.com"),
               folk["people"][0])
    check("a contact records which events they came to",
          who["nights"] >= 1 and who["email"] == "crm@example.com", "got %s" % who)
    check("and what they do, kept on the contact",
          who["occupation"] == "Founder / Business owner", "got %s" % who.get("occupation"))
    status, detail = crew.json("/api/crm/person?id=%s" % who["id"])
    check("one contact opens", status == 200 and detail["email"] == who["email"])

    # The promise the whole shape rests on: nothing joins a person to an answer.
    # If a future change quietly reintroduces that link, this is what fails.
    blob = json.dumps(detail).lower()
    check("a contact record carries no answers of any kind",
          "responses" not in detail and "sentiment" not in blob
          and "is any of this written down" not in blob,
          "leaked: %s" % blob[:180])

    status, found = crew.json("/api/crm/people?q=" + who["email"].split("@")[0])
    check("search finds them", found["total"] >= 1, "total=%s" % found["total"])
    status, none = crew.json("/api/crm/people?q=zzzznobody")
    check("and finds nothing when there is nothing", none["total"] == 0)

    # the cross-tabs — the reason the response half carries occupation at all
    status, report = crew.json("/api/crm/session?id=%s" % over["sessions"][0]["id"])
    check("an event reports what the room thought", "answers" in report)
    for block in report.get("answers", []):
        check("percentages add up (%s)" % block["question"][:24],
              abs(sum(block["pcts"]) - 100) <= 2,
              "%s = %s" % (block["pcts"], sum(block["pcts"])))
        check("the breakdown never exceeds the room (%s)" % block["question"][:24],
              sum(g["total"] for g in block["groups"]) == block["total"],
              "%d in groups vs %d answered"
              % (sum(g["total"] for g in block["groups"]), block["total"]))

    # a report must never count one person twice, however often they changed
    # their mind — the archive keeps every position, the report takes the last
    sess_id = over["sessions"][0]["id"]
    status, report = crew.json("/api/crm/session?id=%s" % sess_id)
    check("an event report opens", status == 200 and "occupations" in report)
    for topic in report.get("topics", []):
        total = topic["agree"] + topic["disagree"] + topic["unsure"]
        check("the room's split never exceeds the room (topic %s)" % topic["topic_index"],
              total <= report["attendees"],
              "%d votes from %d people" % (total, report["attendees"]))

    status, body, headers = crew.get("/api/crm/people.csv")
    check("people export as CSV", status == 200 and b"email,name" in body,
          body[:60].decode("utf-8", "replace"))
    status, resp_csv, _ = crew.get("/api/crm/responses.csv")
    check("the responses export carries no names or addresses",
          b"crm@example.com" not in resp_csv and b"Crm Tester" not in resp_csv
          and b"email" not in resp_csv.split(b"\n")[0],
          resp_csv.split(b"\n")[0][:90].decode("utf-8", "replace"))
    check("the CSV downloads rather than displays",
          "attachment" in headers.get("Content-Disposition", ""))
    status, body, _ = crew.get("/api/crm/responses.csv")
    check("responses export as CSV", status == 200 and b"room,event" in body)
    status, body, _ = crew.get("/api/crm/attendance.csv")
    check("attendance exports as CSV", status == 200 and b"room,event" in body)

    # ---- the recap is public and carries nothing personal ----
    status, recap = guest.json("/api/recap?room=%s" % ROOM)
    blob = json.dumps(recap).lower()
    check("the recap is public", status == 200)
    check("the recap leaks no addresses", "@example.com" not in blob)
    check("the recap leaks no names", "signer" not in blob and "p_one" not in blob)

    # ---- the QR the projector shows ----
    status, body, headers = guest.get("/qr.svg?url=https://example.com/x")
    check("the join QR renders", status == 200 and body.startswith(b"<svg")
          and len(body) > 500, "status=%s len=%s" % (status, len(body)))
    status, white, _ = guest.get("/qr.svg?light=white&url=https://example.com/x")
    check("and on white for paper, so a printer doesn't render a grey square",
          b'fill="#ffffff"' in white and b'fill="#f5f3ef"' in body)

    # ---- a room that is switched off stops taking part ----
    crew.post("/api/rooms/%s" % ROOM, {"closed": True})
    check("a closed room says so", state(guest)["closed"] is True)
    before = state(guest)["sentiment"]["agree"]
    guest.act("sentiment", pid="p_one", choice="agree")
    check("a closed room stops accepting votes",
          state(guest)["sentiment"]["agree"] == before)
    crew.post("/api/rooms/%s" % ROOM, {"closed": False})
    check("and can be reopened", state(guest)["closed"] is False)


def main():
    data_dir = tempfile.mkdtemp(prefix="upgrade-smoke-")
    port = free_port()
    env = dict(os.environ, PORT=str(port), DATA_DIR=data_dir, PASSCODE=PASSCODE,
               PUBLIC_URL="http://127.0.0.1:%d" % port)
    log = open(os.path.join(data_dir, "server.log"), "w+")
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "server.py")],
                            env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/state?room=" + ROOM, timeout=1).read()
                break
            except Exception:
                if proc.poll() is not None:
                    log.seek(0)
                    print("server exited before it was ready:\n" + log.read())
                    return 1
                time.sleep(0.1)
        else:
            print("server never came up")
            return 1

        print("\n\033[1mTHE UPGRADE LIVE — smoke test\033[0m  (port %d)\n" % port)
        run(Client(base), Client(base), data_dir)

        # nothing should have gone bang along the way
        log.seek(0)
        server_log = log.read()
        check("the server logged no tracebacks", "Traceback" not in server_log,
              server_log[-400:] if "Traceback" in server_log else "")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(data_dir, ignore_errors=True)

    print("\n%d passed, %d failed\n" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("\033[31mFAILED:\033[0m")
        for name, detail in FAILED:
            print("  · %s%s" % (name, ("  — " + detail) if detail else ""))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
