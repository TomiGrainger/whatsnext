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
    for path, label in (("/", "audience"), ("/projector", "projector"),
                        ("/recap", "recap"), ("/setup", "setup"),
                        ("/moderator", "moderator")):
        status, body, _ = guest.get(path)
        check("page serves: %s" % label, status == 200, "got %s" % status)
    status, body, _ = guest.get("/moderator")
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
                 "showOffer", "showHolding", "showStats", "removeWord", "reset"):
        status, _ = guest.act(kind, index=0)
        check("guests are refused: %s" % kind, status == 401, "got %s" % status)

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
    crew.act("showHolding", on=True)
    check("the crew can raise the holding screen", state(guest)["holdingLive"] is True)
    crew.act("showHolding", on=False)
    check("and take it down", state(guest)["holdingLive"] is False)

    # ---- state of the room on the wall ----
    crew.act("showStats", on=True)
    check("the crew can put the room's make-up on the wall",
          state(guest)["statsLive"] is True)
    crew.act("showStats", on=False)

    # ---- the offer ----
    status, ev = crew.json("/api/events/demo_event")
    config = ev["config"]
    config["offer"] = {"headline": "Work with me", "body": "Twelve weeks.",
                       "cta": "I'M INTERESTED", "link": "example.com/coaching",
                       "linkLabel": "Details", "image": ""}
    status, body, _ = crew.post("/api/events/demo_event", config)
    check("the offer can be set up", status == 200, body[:120].decode("utf-8", "replace"))
    st = state(guest)
    check("the offer reaches a room that is already open",
          (st.get("offer") or {}).get("headline") == "Work with me")
    check("the offer link is normalised to a real URL",
          (st.get("offer") or {}).get("link", "").startswith("http"))
    crew.act("showOffer", on=True)
    check("the crew can put the offer up", state(guest)["offerLive"] is True)
    _, before_rows = crew.json("/api/interest/%s.json" % ROOM)
    guest.act("interested", pid="p_one", name="One", email="one@example.com")
    guest.act("interested", pid="p_one", name="One", email="one@example.com")
    status, rows = crew.json("/api/interest/%s.json" % ROOM)
    check("raising a hand is recorded once, however many times it is tapped",
          status == 200 and len(rows) == len(before_rows) + 1,
          "before=%s after=%s" % (len(before_rows), len(rows)))
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
    status, leads = crew.json("/api/leads/%s.json" % ROOM)
    check("sign-ups are readable by the crew", status == 200 and len(leads) == 1)
    lead_count = len(leads)
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
        lead_count = len(crew.json("/api/leads/%s.json" % ROOM)[1])

    # ---- unsubscribe and deletion ----
    status, body, _ = guest.get("/unsubscribe?e=sign%40example.com&t=forged")
    check("a forged unsubscribe link is refused", status == 400, "got %s" % status)

    # ---- reset keeps the things that are not rehearsal data ----
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
