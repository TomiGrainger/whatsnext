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
import csv
import hmac
import io
import json
import os
import queue
import re
import secrets
import signal
import smtplib
import socket
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import crm
import qr

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
BUNDLED_EVENTS_DIR = os.path.join(ROOT, "events")

# Anything written at run time lives under DATA_DIR so it can sit on a mounted
# volume when hosted. Defaults to the repo, which is what you want locally.
DATA_DIR = os.environ.get("DATA_DIR") or ROOT
STATE_FILE = os.path.join(DATA_DIR, "rooms_state.json")
LEADS_DIR = os.path.join(DATA_DIR, "leads")

# Events are authored through /setup, so they belong with the other things
# written at run time. Hosted, that puts them on the volume and they survive a
# redeploy; locally DATA_DIR is the repo, so they stay in git as before.
EVENTS_DIR = BUNDLED_EVENTS_DIR if DATA_DIR == ROOT else os.path.join(DATA_DIR, "events")

# Where the audience reaches this server from — the address the QR sends phones
# to. Three sources, in order of trust:
#   1. PUBLIC_URL, if you set it explicitly.
#   2. Learned from the first request that arrives through a reverse proxy. A
#      host sees its own container IP and nothing else, so the only place the
#      real public name exists is the headers the proxy adds. Getting this wrong
#      is a silent failure — a QR pointing somewhere no phone can reach — so the
#      app works it out rather than relying on being told.
#   3. This machine's LAN address, for running it locally on the same Wi-Fi.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or "").strip().rstrip("/")
_learned_url = None
URL_LOCK = threading.Lock()


def learn_public_url(headers):
    """Remember the public address from a proxied request, once."""
    global _learned_url
    if PUBLIC_URL or _learned_url:
        return
    host = (headers.get("X-Forwarded-Host") or headers.get("Host") or "").split(",")[0].strip()
    if not host or host.startswith("localhost") or host.startswith("127.0.0.1"):
        return
    proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        return          # no proxy in front: fall back to LAN detection
    with URL_LOCK:
        if not _learned_url:
            _learned_url = "%s://%s" % (proto, host)
            print("  Public address learned from the proxy: %s" % _learned_url)


def public_base():
    return PUBLIC_URL or _learned_url or ("http://%s:%d" % (lan_host(), PORT))

DEFAULT_ROOM = "WN25"

# ---------------------------------------------------------------------------
# Email. The closing screen promises people a debrief, so there has to be
# something that actually sends it. Any SMTP provider works — set these and the
# SEND DEBRIEF button in setup goes live; leave them unset and it stays disabled
# rather than silently doing nothing.
# ---------------------------------------------------------------------------

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
# 0 keeps everything. A real number is easier to defend than "forever" — see
# RUNBOOK.md — but the archive's whole point is that it keeps, so it is opt-in.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0") or 0)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_TLS = (os.environ.get("SMTP_TLS", "starttls") or "starttls").strip().lower()
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or SMTP_USER
MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO", "").strip()


def mail_configured():
    return bool(SMTP_HOST and MAIL_FROM)

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


def seed_events():
    """Copy the events shipped with the app into the data directory, but only
    ones that aren't there yet. A fresh volume gets the demo event; an event the
    crew authored or edited is never overwritten by a later deploy."""
    if EVENTS_DIR == BUNDLED_EVENTS_DIR:
        return []
    os.makedirs(EVENTS_DIR, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(BUNDLED_EVENTS_DIR)):
        if not name.endswith(".json"):
            continue
        target = os.path.join(EVENTS_DIR, name)
        if os.path.exists(target):
            continue
        with open(os.path.join(BUNDLED_EVENTS_DIR, name)) as src, open(target, "w") as dst:
            dst.write(src.read())
        copied.append(name)
    return copied


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
INTEREST_LOCK = threading.Lock()


def _interest_path(code):
    return os.path.join(DATA_DIR, "interest", sanitize_code(code) + ".json")


def read_interest(code):
    try:
        with open(_interest_path(code)) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def add_interest(code, pid, email, name, kind="offer"):
    """One hand raised, deduped by person. Returns "stored", "duplicate" or
    "failed" — a hand raised but not written down is worse than useless, so the
    phone has to be able to tell the difference."""
    code = sanitize_code(code)
    with INTEREST_LOCK:
        rows = read_interest(code)
        for row in rows:
            if row.get("pid") == pid and row.get("promo", "offer") == kind:
                if email and not row.get("email"):
                    row["email"] = email          # they've since given us one
                    break
                return "duplicate"
        else:
            room = ROOMS.get(code) or {}
            rows.append({
                "pid": pid, "promo": kind, "name": name or "", "email": email or "",
                "at": time.time(), "room": code,
                "eventName": room.get("eventName", ""),
            })
        try:
            os.makedirs(os.path.dirname(_interest_path(code)), exist_ok=True)
            tmp = _interest_path(code) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(rows, fh, indent=2)
            os.replace(tmp, _interest_path(code))
        except OSError as exc:
            print("  (could not save interest: %s)" % exc)
            return "failed"
    return "stored"



def _lead_email(code, pid):
    """The address this phone gave when it signed up for the debrief, so raising
    a hand for the offer needs nothing typed. Only possible since sign-ups
    started recording the phone that made them."""
    if not pid:
        return ""
    for lead in read_leads(code):
        if lead.get("pid") == pid:
            return lead.get("email", "")
    return ""


def has_interest(code, pid):
    """Which promos this phone has already raised a hand for — so a reload
    doesn't offer them the same button as though they hadn't."""
    return sorted({r.get("promo", "offer") for r in read_interest(code)
                   if r.get("pid") == pid})


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


def add_lead(code, email, name, pid=""):
    """Returns "stored", "duplicate", or "failed". The three are genuinely
    different to the person standing there: only the last one means their
    address is not on the list, and they need telling."""
    code = sanitize_code(code)
    with LEAD_LOCK:
        leads = read_leads(code)
        if any(l.get("email", "").lower() == email.lower() for l in leads):
            return "duplicate"
        room = ROOMS.get(code) or {}
        leads.append({
            "email": email,
            "name": name or "",
            "pid": pid or "",
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
            return "failed"
    return "stored"


def _smtp_connect():
    if SMTP_TLS == "ssl":
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
    else:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        if SMTP_TLS == "starttls":
            smtp.starttls()
    if SMTP_USER:
        smtp.login(SMTP_USER, SMTP_PASS)
    return smtp


def _debrief_message(to_addr, name, event_name, brand, recap_url, promos=None):
    msg = EmailMessage()
    msg["Subject"] = "%s — the debrief" % event_name
    msg["From"] = formataddr((brand, MAIL_FROM))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    if MAIL_REPLY_TO:
        msg["Reply-To"] = MAIL_REPLY_TO
    unsub = unsubscribe_url(to_addr)
    msg["List-Unsubscribe"] = "<%s>" % unsub

    hello = "Hi %s," % name if name else "Hi,"
    text = (
        "%s\n\n"
        "Thanks for being part of %s.\n\n"
        "Here's the debrief — every topic, how the room voted, what changed after\n"
        "the discussion, and the questions you put up:\n\n"
        "%s\n\n"
        "— %s\n"
    ) % (hello, event_name, recap_url, brand)
    text += "\n\nDon't want these? %s\n" % unsub
    for promo in (promos or {}).values():
        if not promo.get("headline"):
            continue
        tail = "\n\n---\n\n%s\n" % promo["headline"]
        if promo.get("body"):
            tail += "%s\n" % promo["body"]
        if promo.get("link"):
            tail += "%s\n" % promo["link"]
        text += tail
    msg.set_content(text)

    # A plain, readable HTML part in the same voice as the app
    msg.add_alternative("""<!doctype html>
<html><body style="margin:0;padding:28px;background:#08080a;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#f5f3ef;">
  <div style="max-width:520px;margin:0 auto;">
    <div style="font-size:13px;letter-spacing:.24em;color:#FF2D46;font-weight:700;">THE DEBRIEF</div>
    <h1 style="font-size:30px;line-height:1.15;margin:14px 0 18px;color:#f5f3ef;">%s</h1>
    <p style="font-size:16px;line-height:1.6;color:#a9a9b2;margin:0 0 14px;">%s</p>
    <p style="font-size:16px;line-height:1.6;color:#a9a9b2;margin:0 0 24px;">
      Thanks for being part of it. Here's everything the room said and decided —
      how it voted, what changed after the discussion, and the questions you put up.
    </p>
    <a href="%s" style="display:inline-block;background:#FF2D46;color:#fff;text-decoration:none;
      font-weight:700;letter-spacing:.08em;padding:15px 24px;border-radius:12px;">SEE THE RESULTS &rarr;</a>
    <p style="font-size:13px;line-height:1.6;color:#6e6e78;margin:26px 0 0;">
      Or paste this in: <br><span style="color:#a9a9b2;">%s</span>
    </p>
    %s
    <p style="font-size:12px;color:#6e6e78;margin:26px 0 0;border-top:1px solid #26262b;padding-top:16px;">
      You're getting this because you asked for the debrief at %s.
      <br><a href="%s" style="color:#8b8b90;">Unsubscribe or delete your details</a>.
    </p>
  </div>
</body></html>""" % (html_escape(event_name), html_escape(hello), html_escape(recap_url),
                     html_escape(recap_url), _promos_html(promos),
                     html_escape(event_name), html_escape(unsub)), subtype="html")
    return msg


def _promos_html(promos):
    return "".join(_promo_html(p) for p in (promos or {}).values())


def _promo_html(offer):
    """One pitch as a block at the foot of the debrief. Images are referenced
    absolutely so they still resolve in a mail client, and the block simply
    disappears when the event hasn't set that promo up."""
    if not offer or not offer.get("headline"):
        return ""
    hero = ""
    if offer.get("image"):
        hero = ('<img src="%s/offers/%s" alt="" width="520" '
                'style="width:100%%;max-width:520px;border-radius:12px;display:block;'
                'margin:0 0 16px;">' % (public_base(), html_escape(offer["image"])))
    body = ""
    if offer.get("body"):
        body = ('<p style="font-size:15px;line-height:1.6;color:#a9a9b2;margin:0 0 16px;">%s</p>'
                % html_escape(offer["body"]))
    cta = ""
    if offer.get("link"):
        cta = ('<a href="%s" style="display:inline-block;background:#FF2D46;color:#fff;'
               'text-decoration:none;font-weight:700;letter-spacing:.08em;padding:13px 20px;'
               'border-radius:11px;">%s</a>'
               % (html_escape(offer["link"]),
                  html_escape(offer.get("linkLabel") or "FIND OUT MORE")))
    return ('<div style="margin:30px 0 0;padding:20px;border:1px solid #3a0e15;border-radius:16px;'
            'background:#14090c;">'
            '<div style="font-size:11px;letter-spacing:.2em;color:#FF2D46;font-weight:700;">'
            + html_escape(offer.get("eyebrow") or "WHAT'S NEXT FOR YOU") + "</div>"
            '%s<h2 style="font-size:22px;line-height:1.15;margin:12px 0 10px;color:#f5f3ef;">%s</h2>'
            '%s%s</div>' % (hero, html_escape(offer["headline"]), body, cta))


def send_debrief(code):
    """Send the recap link to everyone who signed up in this room. Returns a
    summary; already-sent addresses are skipped so pressing the button twice
    doesn't mail anyone again."""
    code = sanitize_code(code)
    with LOCK:
        room = ROOMS.get(code)
        event_name = room.get("eventName", "The event") if room else "The event"
        brand = room.get("brand", "THE UPGRADE") if room else "THE UPGRADE"
        promos = room_promos(room) if room else {}
    recap_url = "%s/recap?room=%s" % (public_base(), code)

    with LEAD_LOCK:
        leads = read_leads(code)
    pending = [l for l in leads if not l.get("sentAt") and not is_suppressed(l["email"])]
    if not pending:
        return {"ok": True, "sent": 0, "failed": 0, "skipped": len(leads),
                "message": "Everyone on this list has already had it."}

    sent, failed, errors = 0, 0, []
    try:
        smtp = _smtp_connect()
    except Exception as exc:
        return {"ok": False, "error": "Couldn't reach the mail server: %s" % exc}

    try:
        for lead in pending:
            try:
                smtp.send_message(_debrief_message(
                    lead["email"], lead.get("name", ""), event_name, brand, recap_url, promos))
                lead["sentAt"] = time.time()
                crm.mark_sent(lead["email"])
                sent += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 3:
                    errors.append("%s: %s" % (lead["email"], exc))
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    # record who has had it, so a second press doesn't double-send
    with LEAD_LOCK:
        stored = read_leads(code)
        by_email = {l["email"].lower(): l for l in pending}
        for l in stored:
            match = by_email.get(l["email"].lower())
            if match and match.get("sentAt"):
                l["sentAt"] = match["sentAt"]
        try:
            os.makedirs(LEADS_DIR, exist_ok=True)
            tmp = _leads_path(code) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(stored, fh, indent=2)
            os.replace(tmp, _leads_path(code))
        except OSError as exc:
            errors.append("could not record who was sent to: %s" % exc)

    return {"ok": failed == 0, "sent": sent, "failed": failed,
            "skipped": len(leads) - len(pending), "errors": errors}


# ---------------------------------------------------------------------------
# Profiles. Optional, per phone, per room: a name, what they do, something
# quirky, and a photo. The moderator sees them next to that person's questions
# and challenges; the projector shows one only when the moderator features it.
# They never reach the public recap.
# ---------------------------------------------------------------------------

AVATAR_DIR_NAME = "avatars"
MAX_AVATAR_BYTES = 3 * 1024 * 1024
MAX_OFFER_BYTES = 5 * 1024 * 1024

# Magic bytes -> extension. Only real raster photos: notably no SVG, which is a
# document that can carry script, and no HTML sneaking in under an image name.
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


def sniff_image(blob):
    """Identify a photo by what it actually is, not what it claims to be."""
    for magic, ext, ctype in IMAGE_SIGNATURES:
        if blob.startswith(magic):
            return ext, ctype
    # WEBP is RIFF....WEBP
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None


def avatars_dir():
    return os.path.join(DATA_DIR, AVATAR_DIR_NAME)


def offers_dir():
    return os.path.join(DATA_DIR, "offers")


def save_offer_image(slug, blob):
    """Same checks as a profile photo — identified by its bytes, never by what
    the upload claims — but this one is the crew's own promo artwork."""
    ext, _ = sniff_image(blob)
    if not ext:
        return None, "That file isn't an image we can use — try a JPEG or PNG."
    if len(blob) > MAX_OFFER_BYTES:
        return None, "That image is too big — keep it under 5MB."
    name = "%s.%s" % (re_pid(slug) or "offer", ext)
    try:
        os.makedirs(offers_dir(), exist_ok=True)
        path = os.path.join(offers_dir(), name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except OSError as exc:
        return None, "Couldn't save that image: %s" % exc
    return name, None


def save_avatar(pid, blob):
    ext, ctype = sniff_image(blob)
    if not ext:
        return None, "That file isn't a photo we can use — try a JPEG or PNG."
    if len(blob) > MAX_AVATAR_BYTES:
        return None, "That photo is too big — keep it under 3MB."
    # the filename is built from our own ids, never from anything uploaded
    name = "%s.%s" % (re_pid(pid), ext)
    try:
        os.makedirs(avatars_dir(), exist_ok=True)
        path = os.path.join(avatars_dir(), name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except OSError as exc:
        return None, "Couldn't save that photo: %s" % exc
    return name, None


def re_pid(pid):
    """Participant ids come from the client, so reduce them to a safe token
    before they are ever used in a filename."""
    clean = "".join(ch for ch in (pid or "") if ch.isalnum() or ch in "-_")[:40]
    return clean or "anon"


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
    """The URL a phone should open. Hosted, that's the public address; locally,
    this machine's address on the Wi-Fi (localhost would be useless on a phone)."""
    code = sanitize_code(code)
    if host:
        return "http://%s:%d/?room=%s" % (host, PORT, code)
    return "%s/?room=%s" % (public_base(), code)


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
        # `rounds` holds finished runs of the same poll, so the room can be asked
        # again after the discussion and see how far it moved
        return {"votes": {o["id"]: seed_votes.get(o["id"], 0) for o in item["options"]},
                "rounds": [], "_votes": {}}
    if kind == "wordcloud":
        # `_votes` is pid -> the words that phone added, for the per-phone cap;
        # `banned` remembers what the crew removed so it cannot be re-submitted
        return {"words": dict(seed_data.get("words", {})), "_votes": {}, "banned": []}
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
        "screen": None,     # holding | stats | offer | donate | None
        "started": False,   # until the crew starts, everyone sits in the lobby
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
        # audience Q&A — open all night, ranked by upvotes, separate from the
        # challenge queue (a challenge asks for the mic, a question asks for an answer)
        "questions": [],
        "featuredQuestion": None,
        # pid -> {name, occupation, fact, avatar}
        "profiles": {},
        "featuredProfile": None,
        # connection requests between audience members
        "connections": [],
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


# Two things an event can put on the screen: what you're selling, and what
# you're asking for. Identical machinery — the same fields, the same takeover,
# the same sheet on the phone — so they share one code path rather than two
# copies that drift apart. Only ever one on screen at a time.
# The screens one button each can put on the projector. Exactly one at a time.
SCREENS = ("holding", "stats", "offer", "donate")


def _screen_ready(r, which):
    """A button does nothing if there is nothing behind it — no offer set up,
    or nobody checked in yet to make a room worth showing."""
    if which in PROMOS:
        return bool(room_promo(r, which))
    if which == "stats":
        return room_stats(r)["checkedIn"] > 0
    return True


PROMOS = {
    "offer": {"eyebrow": "TONIGHT ONLY", "cta": "I'M INTERESTED",
              "label": "Show Offer", "hide": "Hide Offer"},
    # Donating is an action somewhere else, so its button opens the link as
    # well as recording the tap. The offer deliberately doesn't: sending someone
    # to a landing page in the middle of an event is how you lose the room.
    "donate": {"eyebrow": "SUPPORT THIS", "cta": "I'D LIKE TO GIVE",
               "label": "Show Donate", "hide": "Hide Donate", "opensLink": True},
}


def _validate_offer(raw, kind="offer"):
    """One promo. Optional — an event without one simply never shows it. Text is
    kept separate from the image so it can be laid out for a phone and a
    projector independently."""
    if not isinstance(raw, dict):
        return None
    headline = _text(raw.get("headline"), "Headline", 80, required=False)
    if not headline:
        return None                      # no headline, no promo
    link = _text(raw.get("link"), "Link", 300, required=False)
    if link and not link.lower().startswith(("http://", "https://")):
        link = "https://" + link
    image = os.path.basename(_text(raw.get("image"), "", 120, required=False))
    return {
        "headline": headline,
        "body": _text(raw.get("body"), "Offer text", 240, required=False),
        "cta": (_text(raw.get("cta"), "Button label", 30, required=False)
                or PROMOS[kind]["cta"]),
        "eyebrow": (_text(raw.get("eyebrow"), "Eyebrow", 30, required=False)
                    or PROMOS[kind]["eyebrow"]),
        "opensLink": bool(PROMOS[kind].get("opensLink")),
        "link": link,
        "linkLabel": _text(raw.get("linkLabel"), "Link label", 40, required=False) or "See the details",
        "image": image,
    }


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

    out = {
        "brand": _text(payload.get("brand"), "Brand", 40),
        "eventName": _text(payload.get("eventName"), "Event name", 60),
        "topics": topics,
    }
    for kind in PROMOS:
        promo = _validate_offer(payload.get(kind), kind)
        if promo:
            out[kind] = promo
    return out


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


def archive_session(room):
    """The archive's handle on this room, started on first use."""
    return crm.session_for(room["code"], room.get("eventId", ""), room.get("eventName", ""))


def reset_room(room):
    """Wipe every tally back to zero, keeping the room, its code and its event.
    Used to clear a rehearsal before the doors open — note this always clears
    fully, including the demo seed numbers, so a reset room starts truly empty."""
    room["topicRuntime"] = [_init_topic_runtime(t, seed=False) for t in room["topics"]]
    room["challenges"] = []
    room["invited"] = []
    room["questions"] = []
    room["featuredQuestion"] = None
    room["profiles"] = {}
    room["featuredProfile"] = None
    room["connections"] = []
    room["simParticipants"] = 0
    # a reset is "back to before the doors opened", so the lobby comes back too
    room["started"] = False
    room["screen"] = None
    _activate_topic(room, 0)
    # the room is wiped, the record is not: the session is marked as a
    # rehearsal and a fresh one begins with the next thing that happens
    crm.discard_session(room["code"])
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
            # a rename is atomic, but only over data the disk has actually taken:
            # without this a host-level kill can leave the new file empty
            fh.flush()
            os.fsync(fh.fileno())
        # keep the last good copy — if the volume ever hands back a corrupt
        # file, the night falls back one autosave instead of starting over
        if os.path.isfile(STATE_FILE):
            try:
                os.replace(STATE_FILE, STATE_FILE + ".bak")
            except OSError:
                pass
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print("  (persist failed: %s)" % exc)


def load_state():
    # the live file first, then the previous autosave — losing five seconds of
    # an event beats losing the whole thing
    for path, note in ((STATE_FILE, ""), (STATE_FILE + ".bak", " from the backup copy")):
        if os.path.isfile(path):
            if _load_state_file(path, note):
                return
    open_room(DEFAULT_ROOM)  # the demo room is always there


def _load_state_file(path, note):
    """Load one state file. Returns False and leaves ROOMS untouched if it is
    unreadable, so the caller can fall back to the previous autosave."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        loaded = data.get("rooms", {})
    except Exception as exc:
        print("  (could not load %s: %s)" % (os.path.basename(path), exc))
        return False
    with LOCK:
        ROOMS.clear()
        for code, room in loaded.items():
            ROOMS[sanitize_code(code)] = room
    if DEFAULT_ROOM not in ROOMS:
        open_room(DEFAULT_ROOM)
    print("  Restored %d room(s)%s" % (len(ROOMS), note))
    return True


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


# Reaction bursts. Anything here lands on the projector, so the set is fixed —
# a guest can't post arbitrary text to the big screen.
BURST_EMOJI = ("🔥", "👏", "❤️", "😂", "🤯", "🙌")
BURST_LIMIT = 40          # per room per second, plenty for a full room
_bursts = {}              # code -> [timestamps]
BURST_LOCK = threading.Lock()


def burst_allowed(code):
    now = time.time()
    with BURST_LOCK:
        recent = [t for t in _bursts.get(code, []) if now - t < 1.0]
        if len(recent) >= BURST_LIMIT:
            _bursts[code] = recent
            return False
        recent.append(now)
        _bursts[code] = recent
        return True


def broadcast_burst(code, emoji):
    """A reaction is a one-off blip, not room state: it goes out as its own tiny
    message rather than re-sending the whole snapshot to everyone."""
    data = json.dumps({"t": "burst", "emoji": emoji})
    with SUB_LOCK:
        subs = [s for s in SUBSCRIBERS.values() if s["code"] == code]
    for s in subs:
        try:
            s["q"].put_nowait(data)
        except queue.Full:
            pass


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
    "poll": {"question": "", "options": [], "round": 1, "rounds": 1},
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
        rounds = rt.get("rounds", [])
        payload = {"question": item["question"], "options": options,
                   "round": len(rounds) + 1, "rounds": len(rounds) + 1}
        if rounds:
            # only the run immediately before, and only if the room already saw it
            prev = rounds[-1]
            if prev.get("revealed"):
                ptotal = max(1, sum(prev["votes"].values()))
                payload["before"] = [{"id": o["id"],
                                      "pct": round(100 * prev["votes"].get(o["id"], 0) / ptotal)}
                                     for o in item["options"]]
        return payload, sum(rt["votes"].values())
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


def _recap_interaction(item, rt):
    """Final numbers for one interaction, for the public recap."""
    kind = item["kind"]
    out = {"kind": kind, "question": item["question"]}
    if kind == "poll":
        total = max(1, sum(rt["votes"].values()))
        out["options"] = [{"label": o["label"], "votes": rt["votes"][o["id"]],
                           "pct": round(100 * rt["votes"][o["id"]] / total)} for o in item["options"]]
        out["responses"] = sum(rt["votes"].values())
        rounds = rt.get("rounds", [])
        if rounds:
            prev = rounds[-1]
            ptotal = max(1, sum(prev["votes"].values()))
            out["before"] = [{"label": o["label"],
                              "pct": round(100 * prev["votes"].get(o["id"], 0) / ptotal)}
                             for o in item["options"]]
            out["rounds"] = len(rounds) + 1
    elif kind == "wordcloud":
        words = sorted(rt["words"].items(), key=lambda kv: -kv[1])[:30]
        out["words"] = [{"text": w, "weight": c} for w, c in words]
        out["responses"] = sum(rt["words"].values())
    elif kind == "emoji":
        out["reactions"] = [{"char": o["char"], "count": rt["counts"][o["id"]]} for o in item["options"]]
        out["responses"] = sum(rt["counts"].values())
    elif kind == "slider":
        out["avg"] = round(rt["sum"] / rt["count"]) if rt["count"] else 0
        out["leftLabel"] = item.get("leftLabel", "")
        out["rightLabel"] = item.get("rightLabel", "")
        out["responses"] = rt["count"]
    elif kind == "ranking":
        items = sorted(({"label": i["label"], "score": rt["scores"][i["id"]]} for i in item["items"]),
                       key=lambda i: -i["score"])
        out["items"] = items
        out["responses"] = rt.get("submissions", 0)
    return out


def recap_payload(code):
    """Everything the public recap page shows. Deliberately built from scratch
    rather than reusing the live snapshot: it covers every topic, not just the
    active one, and it carries no personal data — no emails, and questions and
    challenges appear without the names attached to them."""
    with LOCK:
        r = ROOMS.get(sanitize_code(code))
        if r is None:
            return {"exists": False, "code": sanitize_code(code)}

        topics = []
        for i, topic in enumerate(r["topics"]):
            rt = r["topicRuntime"][i]
            s = rt["sentiment"]
            total = max(1, s["agree"] + s["disagree"] + s["unsure"])
            topics.append({
                "question": topic["question"],
                "sentiment": {
                    "agree": s["agree"], "disagree": s["disagree"], "unsure": s["unsure"],
                    "agreePct": round(100 * s["agree"] / total),
                    "disagreePct": round(100 * s["disagree"] / total),
                    "unsurePct": round(100 * s["unsure"] / total),
                    "any": s["agree"] + s["disagree"] + s["unsure"] > 0,
                },
                "responses": rt["responses"],
                "interactions": [_recap_interaction(item, rt["interactions"][n])
                                 for n, item in enumerate(topic.get("interactions", []))],
            })

        questions = sorted(r.get("questions", []), key=lambda q: (-q["votes"], -q["at"]))
        return {
            "exists": True,
            "code": r["code"],
            "brand": r["brand"],
            "eventName": r["eventName"],
            "topics": topics,
            # text and votes only — who asked what stays in the room
            "questions": [{"text": q["text"], "votes": q["votes"], "answered": q["answered"]}
                          for q in questions[:20]],
            "challengeCount": len(r["challenges"]),
            "topicCount": len(topics),
            # the pitches travel with the debrief — it's the page people open
            # days later, so they keep working long after the room went dark
            "promos": room_promos(r),
        }


def _profile_card(profiles, pid, with_contact=False):
    p = profiles.get(pid, {})
    card = {"pid": pid, "name": p.get("name", ""), "occupation": p.get("occupation", ""),
            "fact": p.get("fact", ""), "initials": p.get("initials", "?"),
            "avatar": p.get("avatar")}
    if with_contact:
        card["email"] = p.get("email", "")
        card["link"] = p.get("link", "")
    return card


def my_view(code, pid):
    """Everything that is this one person's business and nobody else's: their own
    profile, requests waiting on them, and the contact details of people who
    actually accepted. Keyed on their participant id, which only their phone
    has — the same basis the vote tracking already works on."""
    with LOCK:
        r = ROOMS.get(sanitize_code(code))
        if r is None:
            return {"exists": False}
        profiles = r.get("profiles", {})
        mine = profiles.get(pid, {})
        conns = r.get("connections", [])

        incoming, outgoing, accepted = [], [], []
        for c in conns:
            if c["to"] == pid and c["state"] == "pending":
                incoming.append({"id": c["id"], "who": _profile_card(profiles, c["from"])})
            elif c["from"] == pid and c["state"] == "pending":
                outgoing.append(c["to"])
            elif c["state"] == "accepted" and pid in (c["from"], c["to"]):
                other = c["to"] if c["from"] == pid else c["from"]
                accepted.append(_profile_card(profiles, other, with_contact=True))

        return {
            "exists": True,
            "interested": has_interest(code, pid),
            "shared": bool(mine.get("shared")),
            "profile": _profile_card(profiles, pid, with_contact=True) if mine else None,
            "incoming": incoming,          # waiting for you to accept
            "pending": outgoing,           # you asked, they haven't answered
            "connections": accepted,       # both agreed — contact details included
        }


def missing_snapshot(code):
    """A room code nobody opened. Same shape as a real snapshot so every surface
    can render it without special-casing every field — just `exists: false`."""
    payloads = dict(EMPTY_PAYLOADS)
    return {
        "exists": False,
        "code": sanitize_code(code),
        "brand": "THE UPGRADE", "eventName": "",
        "closed": False, "joinUrl": join_url(code),
        "promos": {}, "promo": None, "screen": None, "started": False,
        "runningOrder": [],
        "topic": "", "topicIndex": 0, "topicCount": 0,
        "interactions": [], "activeInteraction": None,
        "mode": "discussion", "revealed": True, "revealable": False, "rerunnable": False,
        "timed": False, "paused": False, "timeRemaining": 0,
        "inRoom": 0, "responses": 0,
        "sentiment": {"agree": 0, "disagree": 0, "unsure": 0,
                      "agreePct": 0, "disagreePct": 0, "unsurePct": 0},
        "sentimentHistory": [],
        "whatsNext": {"votes": 0, "threshold": 10, "remaining": 10, "unlocked": False},
        "challenges": [], "invited": [],
        "questions": [], "featuredQuestion": None,
        "profiles": {}, "featuredProfile": None, "featuredProfilePid": None,
        "directory": [],
        "poll": payloads["poll"], "wordcloud": payloads["wordcloud"],
        "emoji": payloads["emoji"], "slider": payloads["slider"],
        "ranking": payloads["ranking"],
    }


def room_promo(r, kind):
    """Read live from the event rather than the room's frozen copy, so editing
    a promo reaches rooms that are already open."""
    cfg = get_event(r.get("eventId", DEFAULT_EVENT_ID)) or {}
    promo = cfg.get(kind)
    return dict(promo, kind=kind) if promo else None


def room_promos(r):
    return {k: room_promo(r, k) for k in PROMOS if room_promo(r, k)}


def room_offer(r):
    """The promo on screen right now, if the screen is showing one."""
    live = r.get("screen")
    return room_promo(r, live) if live in PROMOS else None


def _featured_profile(r):
    pid = r.get("featuredProfile")
    if not pid:
        return None
    p = r.get("profiles", {}).get(pid)
    if not p:
        return None
    return {"name": p.get("name", ""), "occupation": p.get("occupation", ""),
            "fact": p.get("fact", ""), "initials": p.get("initials", "?"),
            "avatar": p.get("avatar")}


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
            # every promo the event has (so the closing screen can show them
            # all), plus whichever one is on screen right now
            "promos": room_promos(r),
            "promo": room_offer(r),
            # whichever takeover is up, or nothing. One at a time, always.
            "screen": r.get("screen"),
            "started": bool(r.get("started")),
            # the night's running order, so the lobby can show what's coming
            "runningOrder": [t["question"] for t in r["topics"]],

            # aggregate only — no names, nothing that identifies anyone
            "roomStats": room_stats(r),
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
            # the same poll can be put back to the room after the discussion
            "rerunnable": active is not None and items[active]["kind"] == "poll",
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
            # ranked by votes, newest first among ties; _voters never goes out
            "questions": [{k: v for k, v in q.items() if k != "_voters"}
                          for q in sorted(r.get("questions", []),
                                          key=lambda q: (-q["votes"], -q["at"]))],
            "featuredQuestion": r.get("featuredQuestion"),
            # Who's in the room, for the audience directory: only people who
            # opted in, and never their contact details — those move one-to-one
            # through an accepted connection (see /api/me).
            "directory": [
                {"pid": p, "name": v.get("name", ""), "occupation": v.get("occupation", ""),
                 "fact": v.get("fact", ""), "initials": v.get("initials", "?"),
                 "avatar": v.get("avatar")}
                for p, v in sorted(r.get("profiles", {}).items(),
                                   key=lambda kv: kv[1].get("name", "").lower())
                if v.get("shared") and (v.get("name") or v.get("occupation"))
            ],
            # profiles are personal, so the whole set is crew-only; everyone
            # else sees just the one the moderator has deliberately put up.
            # Contact details are stripped even for the crew — the moderator has
            # no need for the room's email addresses.
            "profiles": ({p: {k: val for k, val in v.items() if k not in ("email", "link")}
                          for p, v in r.get("profiles", {}).items()} if crew else {}),
            "featuredProfile": _featured_profile(r),
            # the crew needs to know *which* person is up, to light the right
            # card; the pid stays out of the public payload
            "featuredProfilePid": r.get("featuredProfile") if crew else None,
            "poll": payloads["poll"],
            "wordcloud": payloads["wordcloud"],
            "emoji": payloads["emoji"],
            "slider": payloads["slider"],
            "ranking": payloads["ranking"],
        }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

# Everyone says who they are on the way in: a name, what they do, and how they
# arrived. Three taps, and it turns an anonymous crowd into a room the moderator
# can read. The occupation list is deliberately short — a long dropdown on a
# phone in a dark venue is worse than a rough fit — and DATA_DIR/occupations.txt
# (one per line) replaces it for a venue where these are the wrong buckets.
DEFAULT_OCCUPATIONS = [
    "Founder / Business owner",
    "Senior leader / Exec",
    "Manager / Team lead",
    "Marketing / Creative",
    "Sales / Business development",
    "Tech / Engineering",
    "Finance / Legal",
    "Healthcare",
    "Education / Academia",
    "Public sector / Non-profit",
    "Freelance / Consultant",
    "Student",
    "Between things right now",
    "Retired",
    "Prefer not to say",
]

# How they walked in. Deliberately spread across the range — an event that only
# offers positive options learns nothing it didn't already assume.
VIBES = [
    {"id": "fired", "char": "\U0001F525", "label": "Fired up"},
    {"id": "good", "char": "\U0001F60A", "label": "Good"},
    {"id": "curious", "char": "\U0001F914", "label": "Curious"},
    {"id": "hopeful", "char": "\U0001F91E", "label": "Hopeful"},
    {"id": "calm", "char": "\U0001F60C", "label": "Calm"},
    {"id": "sceptical", "char": "\U0001F643", "label": "Sceptical"},
    {"id": "nervous", "char": "\U0001F62C", "label": "Nervous"},
    {"id": "tired", "char": "\U0001F634", "label": "Knackered"},
    {"id": "swamped", "char": "\U0001F92F", "label": "Overwhelmed"},
    {"id": "stuck", "char": "\U0001F615", "label": "Stuck"},
]
VIBE_IDS = {v["id"] for v in VIBES}


def _site_secret():
    """A stable per-install secret for signing unsubscribe links. Kept on the
    data volume so links in already-sent mail keep working across a redeploy."""
    path = os.path.join(DATA_DIR, "secret.key")
    try:
        with open(path) as fh:
            key = fh.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(key)
        os.chmod(path, 0o600)
    except OSError:
        pass          # not writable — links still work for this process's life
    return key


def mail_token(email):
    return hmac.new(_site_secret().encode(), email.lower().encode(), "sha256").hexdigest()[:32]


def unsubscribe_url(email):
    return "%s/unsubscribe?e=%s&t=%s" % (
        public_base(), quote(email), mail_token(email))


def _suppressed_path():
    return os.path.join(DATA_DIR, "suppressed.txt")


def is_suppressed(email):
    try:
        with open(_suppressed_path()) as fh:
            return email.lower() in {ln.strip().lower() for ln in fh}
    except OSError:
        return False


def suppress(email):
    """Never mail this address again — for this event or any other."""
    if is_suppressed(email):
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_suppressed_path(), "a") as fh:
            fh.write(email.lower() + "\n")
    except OSError:
        pass


def forget_email(email):
    """Remove every trace of an address: the debrief lists, the offer interest
    lists, and the archive — the person, every event they attended and
    everything they did there. Asked for, and then done."""
    email = (email or "").lower()
    removed = crm.forget_person(email)
    with LEAD_LOCK:
        for folder, lock_free in ((LEADS_DIR, True), (os.path.join(DATA_DIR, "interest"), True)):
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for fn in names:
                if not fn.endswith(".json"):
                    continue
                full = os.path.join(folder, fn)
                try:
                    with open(full) as fh:
                        rows = json.load(fh)
                    keep = [r for r in rows
                            if (r.get("email") or "").lower() != email]
                    if len(keep) != len(rows):
                        removed += len(rows) - len(keep)
                        tmp = full + ".tmp"
                        with open(tmp, "w") as fh:
                            json.dump(keep, fh, indent=2)
                        os.replace(tmp, full)
                except (OSError, json.JSONDecodeError):
                    continue
    return removed


def occupations():
    """The dropdown the phones show, overridable on the volume."""
    try:
        with open(os.path.join(DATA_DIR, "occupations.txt")) as fh:
            custom = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        if custom:
            return custom[:40]
    except OSError:
        pass
    return list(DEFAULT_OCCUPATIONS)


def room_stats(r):
    """Who is in the room tonight, in aggregate. Counts everyone who has checked
    in — not who happens to have an open connection this second, which would
    lurch about every time a phone locked itself."""
    occ, vibes = {}, {}
    checked_in = 0
    for p in r["profiles"].values():
        if not p.get("onboarded"):
            continue
        checked_in += 1
        job = p.get("occupation") or ""
        if job:
            occ[job] = occ.get(job, 0) + 1
        v = p.get("vibe")
        if v in VIBE_IDS:
            vibes[v] = vibes.get(v, 0) + 1
    top = sorted(occ.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "checkedIn": checked_in,
        "occupations": [{"label": k, "count": n} for k, n in top],
        "vibes": [{"id": v["id"], "char": v["char"], "label": v["label"],
                   "count": vibes.get(v["id"], 0)} for v in VIBES],
    }


# A word cloud puts audience text straight onto a three-metre screen, so it is
# the one input that is filtered before it lands. The built-in list is short and
# blunt; DATA_DIR/blocklist.txt (one word per line) extends it without a deploy.
BUILTIN_BLOCKED = {
    "fuck", "fucking", "fucker", "shit", "shite", "bitch", "bastard", "cunt",
    "wanker", "prick", "dick", "cock", "pussy", "twat", "slut", "whore",
    "nigger", "nigga", "faggot", "fag", "tranny", "retard", "retarded", "spastic",
    "paki", "chink", "kike", "coon", "raghead", "rape", "rapist", "nazi", "hitler",
    "porn", "pornhub", "sex", "penis", "vagina", "boobs", "tits", "arse", "ass",
    "piss", "crap", "bollocks", "knob", "minge", "wank",
}
# leetspeak and padding are the obvious ways round a word list
_LEET = str.maketrans({"4": "a", "@": "a", "3": "e", "1": "i", "!": "i", "|": "i",
                       "0": "o", "5": "s", "$": "s", "7": "t", "+": "t", "8": "b"})


def blocklist():
    """Built-in words plus anything the crew has added on the volume."""
    words = set(BUILTIN_BLOCKED)
    try:
        with open(os.path.join(DATA_DIR, "blocklist.txt")) as fh:
            for line in fh:
                w = line.strip().lower()
                if w and not w.startswith("#"):
                    words.add(w)
    except OSError:
        pass
    return words


def word_is_blocked(word):
    """Match on a normalised form so f-u-c-k, fu4ck and fuuuuck are all caught."""
    flat = word.translate(_LEET)
    flat = "".join(ch for ch in flat if ch.isalnum())
    # collapse runs of the same letter: "fuuuck" -> "fuck"
    squashed = ""
    for ch in flat:
        if not squashed or squashed[-1] != ch:
            squashed += ch
    banned = blocklist()
    for form in (word, flat, squashed):
        if form in banned:
            return True
    # a blocked word hiding inside a longer one ("shithead")
    if any(b in squashed for b in banned if len(b) >= 4):
        return True
    # digits standing in for letters ("f4ck", "sh1t"): treat every digit as a
    # wildcard against blocked words of the same length. Narrow on purpose —
    # matching on consonants alone would take "count" down with "cunt".
    lowered = word.lower()
    if any(ch.isdigit() for ch in lowered):
        for b in banned:
            if len(b) == len(lowered) and all(
                    c.isdigit() or c == bc for c, bc in zip(lowered, b)):
                return True
    return False


# how many words one phone may add to a single cloud
MAX_WORDS_PER_PHONE = 3

AUDIENCE_ACTIONS = ("join", "sentiment", "whatsnext", "challenge", "poll", "word",
                    "emoji", "slider", "ranking", "ask", "upvote", "burst", "profile",
                    "connect", "connectRespond", "interested", "forgetMe")


def _connection(room, a, b):
    """Any existing request between two people, either direction."""
    for c in room.get("connections", []):
        if {c["from"], c["to"]} == {a, b} and c["state"] != "declined":
            return c
    return None

MAX_QUESTIONS = 200


def act(code, kind, pid, data):
    result = None      # an optional note back to the phone that acted
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

        # If the crew drives the show without pressing START, the event has
        # plainly started — nobody should be left looking at the lobby because
        # a button was skipped.
        if kind in ("launchInteraction", "nextTopic", "prevTopic", "showResults"):
            r["started"] = True

        # Everything a person does is copied to the archive as it happens, so a
        # RESET clears the room without clearing the record. Queued, never
        # written on this thread.
        session = archive_session(r) if kind in AUDIENCE_ACTIONS else None

        def keep(what, value="", value_num=None):
            crm.record(session, pid, what, topic_index=r["topicIndex"],
                       topic_question=topic.get("question", ""),
                       interaction_question=(item or {}).get("question", ""),
                       value=value, value_num=value_num)

        def label_of(options, oid, field="label"):
            # store what it said, not its id — an event can be edited later
            return next((o.get(field, "") for o in options if o["id"] == oid), oid or "")

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
                    keep("sentiment", choice)
                    _push_history(rt)

        elif kind == "whatsnext":
            if pid not in rt["_votes"]["next"]:
                rt["_votes"]["next"].append(pid)
                rt["whatsNext"]["votes"] += 1
                keep("whatsnext")

        elif kind == "challenge":
            text = (data.get("text") or "").strip()[:180]
            profile = r["profiles"].get(pid, {})
            name = (data.get("name") or "").strip()[:40] or profile.get("name", "")
            if text:
                r["challenges"].insert(0, {
                    "id": _cid(), "name": name or "Anonymous",
                    "initials": _initials(name) if name else "?", "text": text, "at": time.time(),
                    "pid": pid,
                })
                r["challenges"] = r["challenges"][:24]
                keep("challenge", text)

        elif kind == "profile":
            name = (data.get("name") or "").strip()[:40]
            occupation = (data.get("occupation") or "").strip()[:60]
            vibe = data.get("vibe") if data.get("vibe") in VIBE_IDS else None
            fact = (data.get("fact") or "").strip()[:120]
            email = plausible_email(data.get("email")) or ""
            link = (data.get("link") or "").strip()[:120]
            # `shared` is the consent switch: nothing about a person reaches the
            # rest of the room unless they turn it on themselves
            shared = bool(data.get("shared"))
            existing = r["profiles"].get(pid, {})
            if name or occupation or fact or email or link:
                r["profiles"][pid] = {
                    "name": name, "occupation": occupation, "fact": fact,
                    "initials": _initials(name) if name else "?",
                    "avatar": existing.get("avatar"),
                    "email": email, "link": link,
                    "shared": shared,
                    # check-in fields: kept across later profile edits
                    "vibe": vibe or existing.get("vibe"),
                    "onboarded": bool(data.get("checkin")) or existing.get("onboarded", False),
                }
                # the anonymous half gets what they do and how they arrived;
                # the name they gave stays in the live room, and reaches the
                # archive only as a contact record if they hand over an address
                crm.check_in(session, pid, occupation, vibe or existing.get("vibe") or "")
                if email:
                    crm.contact(email, name, occupation)
            elif existing:
                # cleared every field — drop everything but a photo they kept
                existing.update({"name": "", "occupation": "", "fact": "", "initials": "?",
                                 "email": "", "link": "", "shared": False})

        elif kind == "forgetMe":
            # everything this phone told the room, gone — including its place in
            # the room's make-up. The email lists are handled separately, from
            # the link in the mail, since the room doesn't know that address.
            r["profiles"].pop(pid, None)
            r["connections"] = [c for c in r.get("connections", [])
                                if pid not in (c.get("from"), c.get("to"))]
            r["questions"] = [q for q in r["questions"] if q.get("pid") != pid]
            r["challenges"] = [c for c in r["challenges"] if c.get("pid") != pid]
            if r.get("featuredProfile") == pid:
                r["featuredProfile"] = None
            crm.forget_pid(session, pid)

        elif kind == "interested":
            # Reuse an address we already have — the debrief sign-up or their
            # profile — so for most people this is genuinely one tap.
            live_promo = room_offer(r)
            if live_promo:
                which = live_promo.get("kind", "offer")
                profile = r["profiles"].get(pid, {})
                given = plausible_email(data.get("email"))
                known = profile.get("email") or _lead_email(r["code"], pid)
                who = (data.get("name") or profile.get("name") or "").strip()[:40]
                outcome = add_interest(r["code"], pid, given or known or "", who, which)
                keep(which, live_promo.get("headline", ""))
                crm.signup(session, given or known or "", who,
                           profile.get("occupation", ""), kind=which)
                # "already on the list" is a yes; only a failed write is a no
                result = {"saved": outcome != "failed"}

        elif kind == "connect":
            # A request, not a transfer. Contact details move only once the other
            # person accepts, so nobody can quietly collect the room's addresses.
            target = re_pid(data.get("to"))
            them = r["profiles"].get(target, {})
            me = r["profiles"].get(pid, {})
            if (target != pid and them.get("shared") and me.get("shared")
                    and not _connection(r, pid, target)):
                r["connections"].append({
                    "id": _cid(), "from": pid, "to": target,
                    "state": "pending", "at": time.time(),
                })

        elif kind == "connectRespond":
            cid = data.get("id")
            accept = bool(data.get("accept"))
            for c in r["connections"]:
                # only the person who received it may answer it
                if c["id"] == cid and c["to"] == pid and c["state"] == "pending":
                    c["state"] = "accepted" if accept else "declined"
                    c["answeredAt"] = time.time()
                    break

        elif kind == "ask":
            text = (data.get("text") or "").strip()[:200]
            profile = r["profiles"].get(pid, {})
            name = (data.get("name") or "").strip()[:40] or profile.get("name", "")
            if text and len(r["questions"]) < MAX_QUESTIONS:
                r["questions"].append({
                    "id": _cid(), "name": name or "Anonymous",
                    "initials": _initials(name) if name else "?",
                    "pid": pid,
                    "text": text, "at": time.time(),
                    "votes": 1,          # asking counts as your own upvote
                    "answered": False,
                    "topicIndex": r["topicIndex"],
                    "_voters": [pid],
                })
                keep("question", text)

        elif kind == "upvote":
            qid = data.get("id")
            for q in r["questions"]:
                if q["id"] == qid:
                    if pid in q["_voters"]:
                        q["_voters"].remove(pid)      # tapping again takes it back
                        q["votes"] = max(0, q["votes"] - 1)
                    else:
                        q["_voters"].append(pid)
                        q["votes"] += 1
                    break

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
                        keep("poll", label_of(item["options"], opt))

        elif kind == "word":
            if live("wordcloud"):
                word = (data.get("word") or "").strip().lower()[:20]
                word = "".join(ch for ch in word if ch.isalnum() or ch in "-'")
                mine = irt["_votes"].setdefault(pid, [])
                if word and len(mine) < MAX_WORDS_PER_PHONE and word not in mine:
                    # A filtered word still spends the sender's allowance. That
                    # keeps the reply identical either way — no way to probe the
                    # filter — and costs a persistent troll their three goes.
                    if (word not in irt.get("banned", [])
                            and not word_is_blocked(word)):
                        irt["words"][word] = irt["words"].get(word, 0) + 1
                        keep("word", word)   # filtered words are not archived either
                    mine.append(word)
                result = {"wordsLeft": max(0, MAX_WORDS_PER_PHONE - len(mine))}

        elif kind == "emoji":
            if live("emoji"):
                oid = data.get("id")
                if oid in irt["counts"]:
                    irt["counts"][oid] += 1
                    keep("emoji", label_of(item["options"], oid, "char"))

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
                    keep("slider", str(val), float(val))

        elif kind == "ranking":
            if live("ranking"):
                order = data.get("order") or []
                n = len(order)
                if n and all(i in irt["scores"] for i in order):
                    for pos, iid in enumerate(order):
                        irt["scores"][iid] += (n - pos)
                    irt["submissions"] = irt.get("submissions", 0) + 1
                    keep("ranking", " > ".join(label_of(item["items"], i) for i in order))

        # ---- moderator: topic flow ----
        elif kind == "launchInteraction":
            try:
                _activate_interaction(r, int(data.get("index")))
            except (TypeError, ValueError):
                pass
        elif kind == "reveal":
            r["revealed"] = True
        elif kind == "askAgain":
            # Put the same poll back to the room. The run just finished is kept
            # as the "before", and everyone votes again from scratch.
            if item is not None and item["kind"] == "poll":
                irt["rounds"].append({"votes": dict(irt["votes"]),
                                      "revealed": r.get("revealed", False)})
                irt["votes"] = {k: 0 for k in irt["votes"]}
                irt["_votes"] = {}
                r["revealed"] = False
                r["paused"] = False
                r["timeRemaining"] = item.get("settings", {}).get("duration", 60)
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
        # ---- moderation ----
        # A live room takes free text and photos from strangers, some of which
        # ends up on a public screen and in the public recap. These remove it
        # outright rather than just hiding it from one surface.
        elif kind == "removeQuestion":
            qid = data.get("id")
            r["questions"] = [q for q in r["questions"] if q["id"] != qid]
            if r.get("featuredQuestion") == qid:
                r["featuredQuestion"] = None
        elif kind == "removeChallenge":
            cid = data.get("id")
            r["challenges"] = [c for c in r["challenges"] if c["id"] != cid]
            r["invited"] = [i for i in r["invited"] if i != cid]
        elif kind == "removeProfile":
            target = re_pid(data.get("target"))
            profile = r["profiles"].pop(target, None)
            if r.get("featuredProfile") == target:
                r["featuredProfile"] = None
            if profile and profile.get("avatar"):
                try:
                    os.remove(os.path.join(avatars_dir(), os.path.basename(profile["avatar"])))
                except OSError:
                    pass


        elif kind == "startEvent":
            r["started"] = True
            r["screen"] = None

        elif kind == "removeWord":
            # pulls it off the projector and stops it coming straight back
            word = (data.get("word") or "").strip().lower()[:20]
            for topic_rt in r["topicRuntime"]:
                for wrt in topic_rt["interactions"]:
                    if "words" in wrt and word in wrt["words"]:
                        del wrt["words"][word]
                        banned = wrt.setdefault("banned", [])
                        if word not in banned:
                            banned.append(word)

        elif kind == "showScreen":
            # One takeover at a time, because there is one projector. Putting a
            # screen up takes down whatever was there; pressing the one that is
            # already up takes it down.
            which = data.get("which")
            if which in SCREENS and _screen_ready(r, which):
                want = data.get("on")
                on = (r.get("screen") != which) if want is None else bool(want)
                r["screen"] = which if on else None

        elif kind == "featureProfile":
            r["screen"] = None
            target = re_pid(data.get("target"))
            if target in r["profiles"]:
                r["featuredProfile"] = None if r.get("featuredProfile") == target else target
        elif kind == "featureQuestion":
            r["screen"] = None
            qid = data.get("id")
            # tapping the live one again takes it off the big screen
            r["featuredQuestion"] = None if r.get("featuredQuestion") == qid else qid
        elif kind == "answerQuestion":
            qid = data.get("id")
            for q in r["questions"]:
                if q["id"] == qid:
                    q["answered"] = not q["answered"]
                    if q["answered"] and r.get("featuredQuestion") == qid:
                        r["featuredQuestion"] = None
                    break

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
    return result


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
    ".mp4": "video/mp4", ".webm": "video/webm", ".jpg": "image/jpeg",
}
PAGES = {"/": "audience.html", "/moderator": "moderator.html",
         "/projector": "projector.html", "/setup": "setup.html",
         "/recap": "recap.html", "/crm": "crm.html"}
# The crew surfaces. The audience page and the projector stay open: the projector
# is a passive display, often on a machine nobody can type on.
PROTECTED_PAGES = ("/moderator", "/setup", "/crm")

UNSUB_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Upgrade LIVE — Your details</title>
<link rel="stylesheet" href="/css/app.css">
<style>
  body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
  .box{max-width:440px;width:100%%;background:#0e0e11;border:1px solid #26262b;
    border-radius:20px;padding:32px;}
  h1{font-family:var(--display);text-transform:uppercase;font-size:30px;margin:0 0 6px;color:var(--ink);}
  .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.24em;color:var(--red);font-weight:700;}
  p{color:var(--muted);font-size:15px;line-height:1.6;}
  .addr{color:var(--ink);font-family:var(--mono);font-size:13px;word-break:break-all;}
  button{width:100%%;border:none;border-radius:12px;padding:15px;font-family:var(--mono);
    font-size:12px;letter-spacing:.14em;font-weight:700;cursor:pointer;margin-top:12px;}
  .stop{background:var(--red);color:#fff;}
  .wipe{background:transparent;border:1px solid #3a0e15;color:var(--red);}
  .done{color:var(--ink);font-size:16px;line-height:1.6;}
</style></head>
<body class="s-recap"><div class="box">%s</div></body></html>"""

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
    def is_secure(self):
        """True when the browser's connection is HTTPS, so the session cookie can
        be marked Secure. Behind a reverse proxy the TLS ends at the proxy, so the
        evidence is the header it adds (Fly, Render, Railway, nginx and Caddy all
        send it). We deliberately trust nothing else: guessing Secure from
        PUBLIC_URL would make the cookie unusable — and login impossible — if the
        app were ever reached over plain HTTP."""
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"

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
        # Location stays a relative path so it works whatever host or scheme the
        # proxy in front of us is serving on.
        secure = "; Secure" if self.is_secure() else ""
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400%s"
                             % (COOKIE, cookie, secure))
        if clear_cookie:
            self.send_header("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0%s"
                             % (COOKIE, secure))
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
        # the first proxied request teaches the app its own public address
        learn_public_url(self.headers)
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
        if path.startswith("/api/interest/"):
            if not self.authed():
                return self._deny()
            code = sanitize_code(path[len("/api/interest/"):].replace(".json", ""))
            body = json.dumps(read_interest(code), indent=2)
            return self._send(200, body, "application/json", {
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="interested-%s.json"' % code,
            })
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
                rooms = []
                for c, r in sorted(ROOMS.items()):
                    leads = read_leads(c)
                    rooms.append({
                        "code": c, "eventId": r.get("eventId", DEFAULT_EVENT_ID),
                        "eventName": r.get("eventName", ""), "closed": r.get("closed", False),
                        "leads": len(leads),
                        "unsent": sum(1 for l in leads if not l.get("sentAt")),
                        # counted apart: a hand raised for the offer and a hand
                        # raised for the ask are different kinds of yes
                        "interest": sum(1 for r in read_interest(c)
                                        if r.get("promo", "offer") == "offer"),
                        "donations": sum(1 for r in read_interest(c)
                                         if r.get("promo") == "donate"),
                    })
            return self._send(200, json.dumps({"rooms": rooms, "mailConfigured": mail_configured()}),
                              "application/json", {"Cache-Control": "no-store"})
        if path == "/unsubscribe":
            qs = parse_qs(parsed.query)
            email = (qs.get("e", [""])[0] or "").strip()
            token = qs.get("t", [""])[0]
            if not email or not hmac.compare_digest(token, mail_token(email)):
                return self._send(400, UNSUB_PAGE % (
                    '<div class="eyebrow">LINK NOT VALID</div>'
                    "<h1>That link didn't work</h1>"
                    "<p>It may have been cut in half by your mail app. "
                    "Reply to the email and we'll sort it by hand.</p>"),
                    "text/html; charset=utf-8")
            body = (
                '<div class="eyebrow">YOUR DETAILS</div>'
                "<h1>Your details</h1>"
                '<p>We have this address from an event you came to:<br>'
                '<span class="addr">%s</span></p>'
                '<form method="POST" action="/unsubscribe">'
                '<input type="hidden" name="e" value="%s">'
                '<input type="hidden" name="t" value="%s">'
                '<button class="stop" name="do" value="stop" type="submit">STOP EMAILING ME</button>'
                '<button class="wipe" name="do" value="delete" type="submit">DELETE MY DETAILS ENTIRELY</button>'
                "</form>"
                "<p style=\"font-size:13px;margin-top:18px\">Stopping keeps you off future "
                "sends. Deleting removes your name and address from everything we hold. "
                "Your answers at the event aren't affected because they aren't yours to "
                "find \u2014 they're kept as anonymous room totals, with nothing linking "
                "them to you.</p>"
            ) % (html_escape(email), html_escape(email), html_escape(token))
            return self._send(200, UNSUB_PAGE % body, "text/html; charset=utf-8",
                              {"Cache-Control": "no-store"})
        if path.startswith("/api/crm/"):
            # the whole CRM is crew-only: it is the one place in the app where
            # a named person's history sits in one view
            if not self.authed():
                return self._deny()
            return self.crm_route(path[len("/api/crm/"):], parse_qs(parsed.query))
        if path == "/api/archive":
            # crew-only, and aggregate: proof on sight that the record is being
            # kept, without exposing a single person's details
            if not self.authed():
                return self._deny()
            return self._send(200, json.dumps(crm.summary()), "application/json",
                              {"Cache-Control": "no-store"})
        if path == "/api/onboarding":
            payload = {"occupations": occupations(), "vibes": VIBES}
            return self._send(200, json.dumps(payload), "application/json",
                              {"Cache-Control": "no-cache"})
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
        if (path.startswith("/css/") or path.startswith("/js/")
                or path.startswith("/assets/") or path.startswith("/media/")):
            return self.serve_static(path.lstrip("/"))
        if path == "/api/me":
            qs = parse_qs(parsed.query)
            code = qs.get("room", [DEFAULT_ROOM])[0]
            pid = re_pid(qs.get("pid", [""])[0])
            return self._send(200, json.dumps(my_view(code, pid)), "application/json",
                              {"Cache-Control": "no-store"})
        if path == "/api/recap":
            # public on purpose — this link goes out in the debrief email
            qs = parse_qs(parsed.query)
            code = qs.get("room", [DEFAULT_ROOM])[0]
            return self._send(200, json.dumps(recap_payload(code)), "application/json",
                              {"Cache-Control": "no-store"})
        if path == "/qr.svg":
            qs = parse_qs(parsed.query)
            # either a room's join link, or an explicit URL (the offer's)
            target = (qs.get("url", [""])[0] or "").strip()
            if target:
                if not target.lower().startswith(("http://", "https://")):
                    target = "https://" + target
                target = target[:300]
            else:
                target = join_url(sanitize_code(qs.get("room", [DEFAULT_ROOM])[0]))
            try:
                svg = qr.render_svg(target, size_px=420)
            except ValueError as exc:
                return self._send(500, str(exc))
            return self._send(200, svg, "image/svg+xml", {"Cache-Control": "no-store"})
        if path.startswith("/offers/"):
            name = os.path.basename(path[len("/offers/"):])
            full = os.path.join(offers_dir(), name)
            if not name or not os.path.isfile(full):
                return self._send(404, "Not found")
            try:
                with open(full, "rb") as fh:
                    blob = fh.read()
            except OSError:
                return self._send(404, "Not found")
            _, ctype = sniff_image(blob)
            if not ctype:
                return self._send(404, "Not found")
            return self._send(200, blob, ctype, {
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            })
        if path.startswith("/avatars/"):
            # served with nosniff and an image type we determined ourselves, so
            # an uploaded file can never be interpreted as markup or script
            name = os.path.basename(path[len("/avatars/"):])
            full = os.path.join(avatars_dir(), name)
            if not name or not os.path.isfile(full):
                return self._send(404, "Not found")
            try:
                with open(full, "rb") as fh:
                    blob = fh.read()
            except OSError:
                return self._send(404, "Not found")
            _, ctype = sniff_image(blob)
            if not ctype:
                return self._send(404, "Not found")
            return self._send(200, blob, ctype, {
                "Cache-Control": "private, max-age=60",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            })
        if path == "/healthz":          # for the host's health check
            return self._send(200, "ok")
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
        if parsed.path == "/unsubscribe":
            return self.handle_unsubscribe()

        is_event_update = parsed.path.startswith("/api/events/")
        is_room_update = parsed.path.startswith("/api/rooms/")
        is_debrief = parsed.path.startswith("/api/debrief/")
        if parsed.path == "/api/avatar":
            return self.handle_avatar(parsed)
        if parsed.path == "/api/offer-image":
            if not self.authed():
                return self._deny()
            return self.handle_offer_image(parsed)
        if (parsed.path not in ("/api/action", "/api/events", "/api/rooms", "/api/leads")
                and not is_event_update and not is_room_update and not is_debrief):
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
        if is_debrief:
            return self.send_debrief_route(parsed.path[len("/api/debrief/"):])
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
        room = get_room(code)
        if room is None:
            return self._send(404, json.dumps({"ok": False, "error": "That room isn't open."}),
                              "application/json", {"Cache-Control": "no-store"})

        if kind == "burst":
            # deliberately not part of act(): a reaction changes no state and
            # must not push a full snapshot to every surface
            emoji = data.get("emoji")
            if emoji in BURST_EMOJI and not room.get("closed") and burst_allowed(sanitize_code(code)):
                broadcast_burst(sanitize_code(code), emoji)
            return self._send(200, json.dumps({"ok": True}), "application/json",
                              {"Cache-Control": "no-store"})
        try:
            # participant ids come from the client, so they're reduced to a safe,
            # bounded token before they key anything (votes, profiles, filenames)
            note = act(code, kind, re_pid(data.get("pid")), data)
        except Exception as exc:
            return self._send(500, json.dumps({"ok": False, "error": str(exc)}), "application/json")
        body = {"ok": True}
        if note:
            body.update(note)
        return self._send(200, json.dumps(body), "application/json", {"Cache-Control": "no-store"})

    def handle_offer_image(self, parsed):
        qs = parse_qs(parsed.query)
        slug = qs.get("slug", ["offer"])[0]
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return self._send(400, json.dumps({"ok": False, "error": "No image received."}),
                              "application/json")
        if length > MAX_OFFER_BYTES:
            return self._send(413, json.dumps(
                {"ok": False, "error": "That image is too big — keep it under 5MB."}),
                "application/json")
        name, err = save_offer_image(slug, self.rfile.read(length))
        if err:
            return self._send(400, json.dumps({"ok": False, "error": err}), "application/json")
        return self._send(200, json.dumps({"ok": True, "image": name}), "application/json",
                          {"Cache-Control": "no-store"})

    def handle_avatar(self, parsed):
        """The photo arrives as the raw request body — no multipart parsing, and
        nothing from the upload is trusted: the type comes from sniffing the
        bytes and the filename is built from our own participant id."""
        qs = parse_qs(parsed.query)
        code = sanitize_code(qs.get("room", [DEFAULT_ROOM])[0])
        pid = re_pid(qs.get("pid", [""])[0])
        room = get_room(code)
        if room is None:
            return self._send(404, json.dumps({"ok": False, "error": "That room isn't open."}),
                              "application/json")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return self._send(400, json.dumps({"ok": False, "error": "No photo received."}),
                              "application/json")
        if length > MAX_AVATAR_BYTES:
            return self._send(413, json.dumps(
                {"ok": False, "error": "That photo is too big — keep it under 3MB."}),
                "application/json")
        blob = self.rfile.read(length)
        name, err = save_avatar(pid, blob)
        if err:
            return self._send(400, json.dumps({"ok": False, "error": err}), "application/json")
        with LOCK:
            profile = room["profiles"].setdefault(
                pid, {"name": "", "occupation": "", "fact": "", "initials": "?", "avatar": None})
            profile["avatar"] = name
            mark_dirty()
        broadcast(code)
        return self._send(200, json.dumps({"ok": True, "avatar": name}), "application/json",
                          {"Cache-Control": "no-store"})

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
        pid = re_pid(data.get("pid"))
        outcome = add_lead(code, email, name, pid)
        if outcome != "failed":
            room = ROOMS.get(code) or {}
            job = (room.get("profiles", {}).get(pid) or {}).get("occupation", "")
            crm.signup(crm.session_for(code, room.get("eventId", ""),
                                       room.get("eventName", "")),
                       email, name, job, kind="debrief")
        if outcome == "failed":
            # never tell someone they are on a list they are not on
            return self._send(503, json.dumps({
                "ok": False,
                "error": "We couldn't save that just now — please try again."}),
                "application/json", {"Cache-Control": "no-store"})
        # an address that already signed up still gets a yes — from the guest's
        # side they are on the list either way
        return self._send(200, json.dumps({"ok": True, "duplicate": outcome == "duplicate"}),
                          "application/json", {"Cache-Control": "no-store"})

    def handle_unsubscribe(self):
        """Acting on someone's own request about their own data — no passcode,
        but the signed token has to match the address it claims."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 4096:
            return self._send(413, "Too large")
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace") if length else "")
        email = (fields.get("e", [""])[0] or "").strip()
        token = fields.get("t", [""])[0]
        want = fields.get("do", [""])[0]
        if not email or not hmac.compare_digest(token, mail_token(email)):
            return self._send(400, UNSUB_PAGE % (
                '<div class="eyebrow">LINK NOT VALID</div><h1>That link didn\'t work</h1>'
                "<p>Reply to the email and we'll sort it by hand.</p>"),
                "text/html; charset=utf-8")
        if want == "delete":
            forget_email(email)
            suppress(email)      # and don't let a later event re-add them
            body = ('<div class="eyebrow">DONE</div><h1>Deleted</h1>'
                    '<p class="done">Your name and address are gone from everything we '
                    "hold, and we won't email you again. Nothing further is needed \u2014 "
                    "what you answered on the night was never stored against you.</p>")
        else:
            suppress(email)
            crm.set_suppressed(email)
            body = ('<div class="eyebrow">DONE</div><h1>Unsubscribed</h1>'
                    '<p class="done">We won\'t email this address again. Your name and '
                    "address are still on the list from the night you came \u2014 use the "
                    "delete option above if you'd rather they weren't.</p>")
        return self._send(200, UNSUB_PAGE % body, "text/html; charset=utf-8",
                          {"Cache-Control": "no-store"})

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

    def send_debrief_route(self, code):
        """Mails the recap link to this room's sign-ups. Crew-only, and it goes
        to real people — the setup page asks for a second click before calling it."""
        code = sanitize_code(code)
        if get_room(code) is None:
            return self._send(404, json.dumps({"ok": False, "error": "That room isn't open."}),
                              "application/json")
        if not mail_configured():
            return self._send(400, json.dumps({"ok": False,
                              "error": "No mail server configured — set SMTP_HOST and MAIL_FROM."}),
                              "application/json")
        result = send_debrief(code)
        status = 200 if result.get("ok") else 502
        return self._send(status, json.dumps(result), "application/json", {"Cache-Control": "no-store"})

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
                # switching the room off ends the evening in the record too
                if room["closed"]:
                    crm.close_session(code)
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

    def crm_route(self, what, qs):
        one = lambda k, d="": (qs.get(k, [d])[0] or d)

        def num(k, d=0):
            try:
                return int(one(k, str(d)))
            except ValueError:
                return d

        if what == "overview":
            return self._json({"summary": crm.summary(), "sessions": crm.sessions()})
        if what == "people":
            search = one("q")
            return self._json({
                "people": crm.people(search, one("sort", "recent"),
                                     limit=num("limit", 200), offset=num("offset")),
                "total": crm.people_count(search),
            })
        if what == "person":
            found = crm.person(num("id"))
            if found is None:
                return self._send(404, json.dumps({"ok": False, "error": "No such person"}),
                                  "application/json")
            return self._json(found)
        if what == "session":
            found = crm.session_report(num("id"))
            if found is None:
                return self._send(404, json.dumps({"ok": False, "error": "No such event"}),
                                  "application/json")
            return self._json(found)
        if what.endswith(".csv"):
            kind = what[:-4]
            header, rows = crm.export_rows(kind, num("id") or None)
            if not header:
                return self._send(404, "Not found")
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(header)
            writer.writerows(rows)
            name = "%s-%s.csv" % (kind, time.strftime("%Y-%m-%d"))
            return self._send(200, buf.getvalue(), "text/csv; charset=utf-8", {
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="%s"' % name,
            })
        return self._send(404, "Not found")

    def _json(self, payload):
        return self._send(200, json.dumps(payload), "application/json",
                          {"Cache-Control": "no-store"})

    def serve_static(self, rel):
        rel = rel.replace("..", "")
        full = os.path.join(PUBLIC, rel)
        if not os.path.isfile(full):
            return self._send(404, "Not found")
        ext = os.path.splitext(full)[1]
        ctype = STATIC_TYPES.get(ext, "application/octet-stream")
        if ext in (".mp4", ".webm"):
            return self.serve_media(full, ctype)
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype, {"Cache-Control": "no-cache"})

    def serve_media(self, full, ctype):
        """Video needs byte ranges: Safari asks for one before it will play a
        <video> at all, and refuses the file outright if the server answers 200
        to a Range request. Small files, so the read stays simple."""
        size = os.path.getsize(full)
        rng = self.headers.get("Range", "")
        start, end = 0, size - 1
        partial = False
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip()) if rng else None
        if m:
            first, last = m.group(1), m.group(2)
            if first:
                start = int(first)
                if last:
                    end = min(int(last), size - 1)
            elif last:                      # bytes=-500 — the tail
                start = max(0, size - int(last))
            if start >= size or start > end:
                return self._send(416, "", ctype, {"Content-Range": "bytes */%d" % size})
            partial = True
        with open(full, "rb") as fh:
            fh.seek(start)
            body = fh.read(end - start + 1)
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=3600"}
        if partial:
            headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
        self._send(206 if partial else 200, body, ctype, headers)

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
    crm.init(DATA_DIR)
    brought_in = crm.import_legacy(LEADS_DIR, os.path.join(DATA_DIR, "interest"))
    if brought_in["people"] or brought_in["signups"]:
        print("  Archive: brought in %d person/people and %d sign-up(s) from the old files"
              % (brought_in["people"], brought_in["signups"]))
    if RETENTION_DAYS:
        dropped = crm.purge_older_than(RETENTION_DAYS)
        if dropped:
            print("  Archive: dropped %d session(s) past the %d-day retention"
                  % (dropped, RETENTION_DAYS))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        copied = seed_events()
    except OSError as exc:
        raise SystemExit("  Cannot write to DATA_DIR %s: %s" % (DATA_DIR, exc))
    load_events()
    if DEFAULT_EVENT_ID not in EVENTS:
        raise SystemExit("  No %s.json found in %s — cannot start."
                         % (DEFAULT_EVENT_ID, EVENTS_DIR))
    print("\n  THE UPGRADE LIVE  —  real-time server running")
    print("  Loaded %d event(s): %s" % (len(EVENTS), ", ".join(sorted(EVENTS))))
    if copied:
        print("  Seeded onto the data dir: %s" % ", ".join(copied))
    print("  Data dir: %s   Events: %s" % (DATA_DIR, EVENTS_DIR))
    load_state()
    threading.Thread(target=ticker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    if PUBLIC_URL:
        print("  • Set up     %s/setup       (crew)" % PUBLIC_URL)
        print("  • Moderator  %s/moderator   (crew)" % PUBLIC_URL)
        print("  • Projector  %s/projector" % PUBLIC_URL)
        print("  • Audience   %s/   ← what phones scan" % PUBLIC_URL)
    elif os.environ.get("DATA_DIR"):
        # hosted, but nobody told us the address — we'll learn it from the first
        # request that comes through the proxy
        print("  • Waiting for the first request to learn the public address")
        print("    (or set PUBLIC_URL to pin it explicitly)")
    else:
        print("  • Set up     http://localhost:%d/setup       (crew)" % PORT)
        print("  • Moderator  http://localhost:%d/moderator   (crew)" % PORT)
        print("  • Projector  http://localhost:%d/projector" % PORT)
        print("  • Audience   http://%s:%d/   ← what phones scan" % (lan_host(), PORT))
        print("    (set PUBLIC_URL when hosted so the QR points at the public address)")
    print("\n  CREW PASSCODE: %s%s" % (
        PASSCODE, "" if os.environ.get("PASSCODE") else "   (set PASSCODE=… to choose your own)"))
    print("  Default room: %s   (add ?room=CODE for others)   Ctrl+C to stop\n" % DEFAULT_ROOM)
    # A host redeploying sends SIGTERM, not Ctrl+C. Without this, anything since
    # the last autosave — a room opened seconds ago, the last votes cast — would
    # be lost on every deploy and restart.
    def stop(signum, _frame):
        print("\n  Saving & stopping (%s)…" % signal.Signals(signum).name)
        mark_dirty()
        save_state()
        crm.close()      # anything queued in the last second still gets written
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop(signal.SIGINT, None)


if __name__ == "__main__":
    main()
