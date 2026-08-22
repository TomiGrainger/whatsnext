# THE UPGRADE LIVE

A real-time audience-engagement platform for live debates — think Slido/Mentimeter,
built to the designs in [`Designs/`](Designs). Three synchronized surfaces share one
authoritative live room:

| Surface | URL | Purpose |
|---|---|---|
| **Setup** (desktop) | `/setup` | Build an event, then open it in a room |
| **Audience** (mobile) | `/` | Join, react, challenge, vote — 8 interaction screens |
| **Moderator** (desktop) | `/moderator` | Control Room: sentiment, "what's next", challenge queue, launch interactions |
| **Projector** (big screen) | `/projector` | Room Display for the audience to watch |
| **Recap** (public) | `/recap?room=CODE` | The debrief — results from the whole event |

## Starting a new event

Open <http://localhost:8000/setup>:

1. **New event** — enter a brand and event name, then add topics in running order.
   A topic *is* its discussion question: every topic opens on that discussion,
   which has no countdown and stays up until the moderator moves. Underneath each
   topic, add the interactions you want to be able to launch during it — poll,
   word cloud, slider, emoji, ranking — each with its own question, options and
   seconds on screen. Then, if you're selling something, fill in **the offer**
   at the foot of the form — headline, a line of copy, a button label, a link
   and artwork. Leave the headline empty and there is no offer. Save it.
2. **Go live** — pick that event, type a room code, and hit `OPEN ROOM`. You get
   links to the audience, moderator and projector surfaces for that room.

Rooms exist only once you open them here. A `?room=CODE` nobody has opened — a
stale tab, a mistyped code — shows a plain "room not open" screen and is refused
by the API, so nothing can conjure a room mid-event. Surfaces already parked on a
code switch themselves on the moment you open it, so the projector can be set up
before the room exists. `WN25` is the built-in demo room, and it's the only one
that starts with the mockup's numbers in it.

Each event in the list also has **✎ edit** (loads it back into the builder,
topics and nested interactions alike; `SAVE CHANGES` writes to the same file) and
**× delete** (click once, then ✓ to confirm). Editing or deleting an event never
disturbs a room that is already running — the room holds its own copy. The one
exception is the offer, which is read live, so you can add or fix a promotion
without reopening the room. The demo
event can't be deleted, since it is the fallback for room codes that were never
set up.

Running rooms are listed at the bottom of **Go live** with a **TURN OFF / TURN ON**
switch and a **RESET**. A room that is off shows a "room closed" screen on the
phones and the projector and stops accepting votes, but keeps every tally — switch
it back on and it resumes exactly where it was. **RESET** (two taps, it's
destructive) wipes every vote, tally and challenge back to zero and returns to
topic 1, keeping the room and its event — that's the one to use after a rehearsal,
before the doors open. Note a reset room starts genuinely empty, demo seed numbers
included.

## Hosting it

Locally, phones have to be on the same Wi-Fi. Host it and they can join over
their own mobile data instead — no venue network, no captive portal. See
[DEPLOY.md](DEPLOY.md) for the steps (Fly.io, roughly $3–5/month).

Configuration is all environment variables; you need none of them locally:

| Variable | What it does | Default |
|---|---|---|
| `PASSCODE` | Crew passcode for `/moderator` and `/setup` | random 6 digits, printed at startup |
| `PUBLIC_URL` | Pin the public base URL for the QR and join links | learned from the proxy, else the LAN address |
| `DATA_DIR` | Where `rooms_state.json`, `leads/` and `events/` are written | the project directory |
| `SMTP_*` / `MAIL_FROM` | Mail server for the debrief email — see [DEPLOY.md](DEPLOY.md) | unset — sending disabled |
| `DATA_DIR/avatars/` | Where uploaded profile photos are written | alongside the other run-time data |
| `PORT` | Port to listen on | `8000` |

The app keeps room state in memory and in one file, so it must run as a **single
instance** — don't scale it out.

## Passcode

The **moderator** and **setup** pages are crew-only, behind one shared passcode.
The server prints it at startup; set your own with `PASSCODE=…`:

```bash
PASSCODE=4821 python3 server.py
```

Signing in once per device covers both pages for a day (`/logout` ends it). The
**audience page and the projector stay open** — guests just scan and join, and the
projector is usually on a machine nobody can type on.

The passcode also guards every write: creating, editing and deleting events,
opening/closing/resetting rooms, and all the moderator's own actions (next topic,
launch interaction, reveal, pause…). Audience actions — voting, words, emoji,
challenges — stay open, so the gate never gets between a guest and the debate.
It's one shared code held in memory, not an account system: restarting the server
signs the crew out, and anyone with the code has the controls.

## On the night

- **The bare address finds tonight's room.** `whatsnext.fly.dev` on its own
  redirects to the most recently opened room that is still on — and the setup
  page marks that room **TONIGHT**, so it is never a guess. It used to land
  everyone in the built-in demo room, which is the worst possible failure: the
  demo is always open and full of seeded numbers, so someone would believe they
  had joined and vote into a room nobody was reading. An explicit `?room=` is
  never redirected away — older links still mean what they said.
- **Getting in** — the shortest path we could make it. The QR carries
  `/WN25` — a path, not a query string, so it can be read out from a stage:
  "whatsnext dot fly dot dev slash W N 2 5". Scanning it skips the join screen
  entirely, because asking someone to confirm the room they just scanned is a
  screen for nothing; the join screen is still there for anyone typing the bare
  address. A small QR sits in the corner of the projector for the whole event,
  so someone arriving twenty minutes late has a way in — before this, the code
  only existed before the start and after the close. `/print` (crew-only) lays
  out a door poster and four table cards on white paper, so nobody has to see
  the screen at all. And a phone that has been before fills in what it already
  knows: check-in becomes "still you?" and two taps.
- **Joining** — the projector shows a **SCAN TO JOIN** screen with a QR code
  whenever nobody is in the room yet, and again on the closed screen at the end.
  The code points at whatever address the room can actually reach: hosted, the
  app learns its public name from the first request through the proxy; locally,
  it uses this machine's address on the Wi-Fi. `PUBLIC_URL` pins it if you'd
  rather be explicit. The QR is generated by `qr.py` — standard library only, no
  dependency.
- **Reveal moments** — a **poll** or **slider** opens with its results locked. The
  room votes blind (phones show their own choice, the projector shows
  `RESULTS LOCKED` and a live count of votes in), and nothing is given away: the
  figures are stripped out of the data those screens receive, so there is nothing
  to peek at. The moderator sees the real numbers the whole time and presses
  **REVEAL RESULTS** — the bars fill and the numbers count up on the projector.
  Word clouds, emoji and rankings stay live throughout, since watching those build
  is the point. Re-launching an interaction locks it again.
- **The debrief sign-up** — when a room is switched off, phones show a closing
  screen offering the debrief and full results in exchange for an email, and the
  projector shows the same offer beside the QR so late-comers can still scan in.
  Addresses land in `leads/<ROOM>.json` next to the event that collected them,
  deduplicated per room and stamped with the event name. The setup page shows a
  **✉ count** against each room; click it to download that room's sign-ups.
  Sign-ups need no passcode (guests are not crew), but reading them does.
- **Checking in** — joining now asks three things before the debate: a name, what
  they do (a dropdown — `DATA_DIR/occupations.txt`, one per line, replaces the
  built-in list for a venue where those are the wrong buckets), and how they are
  arriving, picked from ten emoji spread deliberately across the range from
  *fired up* to *stuck*. Three taps, and an anonymous crowd becomes a room the
  moderator can read. It writes into the same profile they can edit later.
- **State of the room** — the control room carries a live breakdown of who is in:
  occupations as bars, vibes as counts, and a total. **STATE OF THE ROOM** puts
  the same picture on the projector — a genuinely good opening moment, and the
  fastest way to show a room what it is made of. Aggregate only: no names, and
  nothing that ties a job or a mood back to a person. It counts everyone who has
  checked in rather than who has a live connection this second, so it doesn't
  lurch about every time a phone locks itself.
- **Profiles** — a red **ADD YOUR PROFILE** bar on the discussion screen until
  someone makes one, after which it steps aside and the header avatar edits it.
  Name, what they do, a quirky fact, a photo. The moderator sees it beside that
  person's questions and challenges, and **SHOW WHO** puts them on the projector.
  Photos are checked by their actual bytes (JPEG/PNG/GIF/WEBP only — no SVG),
  capped at 3MB, stored under `DATA_DIR/avatars/`, and served with `nosniff` so an
  upload can't be interpreted as markup.
- **Who's in the room** — a directory guests opt into. Tick *show me in the room
  directory* and your name, job, fact and photo become visible to other guests who
  have also opted in; leave it off and only the crew ever sees your profile.
  Nothing is shared by default, and browsing is reciprocal — you appear to see.
  Contact details work by handshake: tap **CONNECT**, the other person accepts or
  ignores, and only then do both sides see each other's email and link, with
  **SAVE TO CONTACTS** producing a vCard stamped with where you met. Nobody can
  collect the room's addresses: a request is a request, not a transfer, and none
  of it reaches the public recap or the moderator.
- **The lobby** — from checking in until you press **START THE EVENT**, phones
  show the room filling up: how many are in, what they do, how they're arriving,
  and tonight's running order. It beats a countdown because it is about them,
  and it makes a half-empty room feel full. The projector holds the join code
  with the same picture building underneath, so the wall is never showing the
  first debate question before it has been asked. Driving the show — launching
  anything, moving topic — starts the event too, so nobody is left in the lobby
  because a button was skipped. A RESET puts the room back there.
- **One screen at a time** — **HOLDING**, **STATE OF THE ROOM**, **SHOW OFFER**
  and **SHOW DONATE** are one exclusive set: there is one projector, so putting
  one up takes down whatever was there, and pressing the armed one takes it
  down. The room holds *which* screen is up rather than a flag each, so the lit
  button is always the thing actually on the wall. Putting a question or a
  person on the big screen clears it too — otherwise it lands behind the
  takeover and appears to do nothing.
- **The holding screen** — **HOLDING SCREEN** in the control room throws a
  full-screen video loop over the projector: before doors, during the break,
  while you fix something. Press again and the room comes straight back to
  whatever was on. It sits above every other screen, so it covers a poll, the
  offer and the join code alike, and it plays silently — browsers refuse to
  autoplay anything with sound, and there is nobody at the projector to press
  play. A projector opened or reloaded while it's up joins the loop already
  running. The clip is `public/media/holding.mp4`; drop a different file at that
  path to change it (1920×1080 H.264 plays everywhere, and it ships inside the
  image, so it needs no upload and works even if the venue Wi-Fi is grim).
- **The offer, and the ask** — two promotions per event, set up under the topics:
  a headline, a line of copy, a button label, a link and artwork each. **YOUR
  OFFER** is the thing you're selling; **YOUR ASK** is a donation. Identical
  machinery, its own button in the control room, and only ever one on the screen
  at a time — putting one up takes the other down.

  Either takes over the projector (with a QR of the link for anyone not on their
  phone) and rises as a sheet on every phone in the room. Both reappear on their
  own on the closing screen, in the debrief email and on the recap page, so they
  keep working after the room goes dark.

  The one place they differ: tapping **I'M INTERESTED** records interest *in the
  app* rather than sending anyone to a landing page mid-event — where an address
  is already known, from a profile or the debrief sign-up, it is one tap and
  nothing to type. Tapping **I'D LIKE TO GIVE** records the same thing *and*
  opens the link, because giving is an action that happens somewhere else.

  An address given to either one **also joins that room's debrief list**, and
  the sheet says so before they tap. Someone who hands you their email at your
  event expects the results of the night they attended; leaving them on a list
  that never gets sent is the worst of both — you hold their address and they
  hear nothing. The lists stay separate for their own job: following up an
  offer is not the same as mailing the results.

  Hands raised land in `interest/<ROOM>.json` tagged with which promo they were
  for, shown as separate **💼** and **❤️** counts against the room in setup and
  downloadable there. They are kept apart from the debrief list and survive a
  room RESET, so rehearsing never destroys real leads. Editing either reaches
  rooms that are already open, so a typo can be fixed mid-event.
- **Questions from the floor** — guests ask and upvote from the discussion
  screen; the moderator's ranked panel can throw one over the projector (`PUT UP`)
  or grey it off once covered (`DONE`). Separate from the challenge queue: a
  challenge asks for the mic, a question asks for an answer.
- **The word cloud is the one moderated input** — it is the only place audience
  text goes straight onto a three-metre screen, so submissions are filtered
  (a built-in list plus `DATA_DIR/blocklist.txt`, matched against leetspeak,
  padding and digit substitutions) and capped at three words per phone. A
  filtered word spends the sender's allowance and gets exactly the same reply as
  one that landed — there is no way to probe the filter, and a determined troll
  burns their three goes on nothing. No filter is airtight, so the control room
  lists every word on the wall while a cloud is live: tap one to pull it off the
  screen, and it can't be submitted again for the rest of that run.
- **Taking something down** — every question and challenge has a **REMOVE**, and
  every profile an **✕**. One click, no confirmation, because when something
  needs to come off the screen it needs to come off now. Removal is total: it
  disappears from the phones, the projector and the public recap, and a removed
  profile's photo is deleted from disk. Crew-only, like every other control.
- **Reactions** — hold an emoji on the phone and it streams up the projector.
  Only a fixed set is accepted and each room is capped at 40 a second, so the big
  screen can't be flooded or made to show arbitrary text.
- **Ask again** — re-run the live poll after the discussion. The room votes
  blind a second time, and the reveal shows the shift per option against where it
  started, so a change of mind is visible.
- **The recap** — `/recap?room=CODE` builds itself from the room: every topic
  with its sentiment, each interaction's final numbers, mind-change shifts, word
  clouds and the questions that were asked. It's public on purpose — that's the
  link the debrief email carries — and it deliberately carries no personal data:
  no emails, and questions appear without the names attached. The audience's
  closing screen links straight to it, and each room in setup has an `↗ recap`.
- **Sending the debrief** — **SEND DEBRIEF** against a room in setup emails the
  recap link to everyone who signed up. Two clicks, because it reaches real
  inboxes, and it remembers who has had it so a second press only catches people
  who signed up since. Needs `SMTP_HOST` and `MAIL_FROM`; without them the button
  is disabled and says why rather than quietly doing nothing.
- **Dropped phones** — the audience surface watches its own connection. If a phone
  loses Wi-Fi it shows a small `RECONNECTING…` chip, keeps the current screen
  usable, and rebuilds the stream by itself once the network is back (including
  after screen-lock or a tab switch). Taps made during a dropout are retried
  briefly rather than silently lost.

Events are stored as JSON in [`events/`](events) — one file per event, written by
the setup page. You can also author or edit them by hand; they are picked up on
restart. Each room takes its own copy of the event when it opens, so editing an
event never disturbs a room that is already running, and two rooms can run two
different events at the same time.

## Run it

No dependencies, no build step — just Python 3 (already on macOS):

```bash
python3 server.py
```

Then open:

- Audience  → <http://localhost:8000/>
- Moderator → <http://localhost:8000/moderator>
- Projector → <http://localhost:8000/projector>

Room code: **WN25**. Set a different port with `PORT=9000 python3 server.py`.

Open the moderator on a laptop, the projector on a second screen, and the audience
on phones (same Wi-Fi, visit `http://<your-laptop-ip>:8000/`). Everything updates
live for everyone at once.

## How it works

- **Backend** — `server.py`, pure Python standard library. A threaded HTTP server
  serves the static surfaces and exposes:
  - `GET /events` — a **Server-Sent Events** stream that pushes the full room
    snapshot to every connected surface whenever anything changes (and once a second
    to drive the countdown timer).
  - `POST /api/action` — every audience/moderator action (vote, challenge, launch
    poll, next topic, …) mutates the in-memory room, then broadcasts to all surfaces.
  - `GET /api/state` — a plain JSON snapshot (used as a fallback / for debugging).
  - `GET /api/events` · `POST /api/events` — list events, or save a new one
    (validated, then written to `events/<slug>.json`).
  - `GET`/`POST`/`DELETE /api/events/<id>` — read, update, or delete one event.
  - `GET /api/rooms` · `POST /api/rooms` — list running rooms, or open a room
    bound to an event.
- **Frontend** — buildless vanilla JS. All three pages share `public/js/live.js`
  (the SSE + fetch client) and `public/css/app.css` (the design system: Anton display
  type, Space Mono labels, the crimson-on-black palette).
- **Event config vs. runtime state** — an event file holds *content only*
  (branding, topics, questions, options). Everything that changes while the room
  is live — votes, tallies, the challenge queue, timers, "what's next" — is
  runtime state, tracked per topic so each poll or slider counts separately.
- **The demo event** ships pre-seeded to match the mockups (sentiment 41/37/22,
  poll 42/28/17/13, the emoji counts, the challenge queue) so every surface looks
  alive on first load. That seed data belongs to `events/demo_event.json` alone —
  events you create start from zero.

## The moderator drives the show

The Control Room mirrors how the event was set up. Under the current topic sits a
row of buttons: **Discussion** (always first, no timer) followed by exactly the
interactions that topic was given, in order, each showing its length. Tap one and
every phone and the projector switch to it instantly and its countdown starts;
tap Discussion to come back. `PREV` / `NEXT TOPIC` walk the running order, and
pause/extend only apply while a timed interaction is up.

Audience votes flow back the other way into the sentiment ring, the "what's next"
counter, and the challenge queue. Each interaction keeps its own tallies, so
returning to one later shows what it had.

## The archive

Everything that happens at an event is copied to `DATA_DIR/crm.sqlite3` as it
happens — SQLite from the standard library, so the zero-dependency rule still
holds. Writes are queued and flushed a second at a time; measured at 1,300
votes/second with 300 simultaneous voters.

It is deliberately **two halves that never touch**:

```
people      name, email, occupation — who to contact, and nothing else
signups     which evenings someone came to, and whether they raised a hand

sessions    one run of an event in a room
attendees   one anonymous person-shaped row per event: occupation, vibe
responses   every vote, word, slider, ranking, question and challenge
```

There is no join between them, and that is the whole design. An attendee is
identified by a hash of the phone's token **and the event**, so one person's
answers group within a single evening — you can ask whether people who arrived
sceptical shifted more than people who arrived fired up — while the same phone
at the next event is a different, unlinkable row. The raw token is never stored.

Two consequences, both intended:

- Once an evening is over, **nobody — including you — can find out what any
  named person said.** The contact panel in `/crm` says so on its face.
- A deletion request removes the contact record and the sign-up log. It doesn't
  touch the answers, because there is no path from an address to one. There is
  nothing personal left in them to remove.

What you get instead is the thing worth having: **what the room thought, and who
thought it.** Every question gets an overall split as counts and percentages,
then the same split per occupation — "founders 78% agree, students 86%
disagree". Groups smaller than three are folded into an "Other" row, because a
breakdown showing one Retired person who disagreed is a name to anyone who was
in the room.

Reports take each person's **last word** on a question. The archive keeps every
position they held — watching minds change is the point of the app — but a
report that counted them all would turn someone who switched sides twice into
three people.

Existing `leads/` and `interest/` files are folded in on first boot, once. An
archive written under the older, identified shape is migrated on startup: the
person links and check-in names come off the attendee rows, occupations move
across to the contact records, and the tokens become per-event hashes. One-way,
on purpose.

`RETENTION_DAYS` drops sessions older than N days at boot, defaulting to 0.
Crew-only `/api/archive` reports what it holds.

### The Rooms — `/crm`

The crew page over the archive, behind the same passcode as everything else.

- **People** — the contact list. Name, email, what they do, how many events they
  came to and how many times they raised a hand. Searchable and sortable. Not
  what they said, ever.
- **Events** — one row per evening, with a report behind it: who was in the room
  by occupation, how it split on each topic, the cross-tabs above, and what was
  asked. The view to hand a venue or a sponsor. Rehearsals are labelled and left
  out of the totals.
- **Export** — `people.csv` (contacts), `attendance.csv` (who came to what) and
  `responses.csv` (anonymous answers, safe to hand to an analyst). Plain CSV, so
  it imports into HubSpot or Mailchimp if you ever move.

## Tests

```bash
python3 test_smoke.py
```

Boots a real server on a spare port against a throwaway data directory, drives
it the way an event would, and asserts what comes back — 103 checks in about a
third of a second, standard library only. It is black-box on purpose: it talks
HTTP and never imports the app's internals, so it survives the code underneath
being rearranged.

Two halves matter most. Every crew action is fired *without* a passcode and must
be refused; every audience action is fired without one and must be accepted. The
second half is not symmetry for its own sake — `join` once slipped out of the
audience list and sent every phone that scanned the QR to the crew passcode
screen, and a suite that only checked the crew gate would have passed happily
through it. It now fails within a second.

Run it before every deploy.

**✉ preview** on each room in setup renders the exact debrief email that room
would send, without needing mail to be working — which is usually the thing you
are trying to check. [RUNBOOK.md](RUNBOOK.md) covers the other half —
what actually happens when the machine dies, the volume fills up or a phone
loses signal mid-vote, each one measured rather than assumed.

## Files

```
server.py              real-time server (stdlib only)
crm.py                 the archive — SQLite, every event and everyone in it
test_smoke.py          end-to-end smoke test — run before every deploy
RUNBOOK.md             what to do on the night when something breaks
qr.py                  minimal QR encoder for the projector's join code
Dockerfile             container image for hosting
fly.toml               Fly.io config — see DEPLOY.md
events/
  demo_event.json      the demo event — also the template for new ones
public/
  media/holding.mp4    the projector's holding-screen loop
  setup.html           event builder + room launcher
  crm.html             The Rooms — the crew's view of the archive
  print.html           door poster + table cards for a room's join code
  audience.html        mobile surface — 8 interaction screens
  moderator.html       control room dashboard
  projector.html       room display
  css/app.css          shared design system
  css/{audience,moderator,projector,setup}.css
  js/live.js           shared SSE + action client
  js/{audience,moderator,projector,setup,crm}.js
Designs/               the visual source of truth (do not redesign)
events/*.json          event content — one file per event
leads/<ROOM>.json      debrief sign-ups, written at the event (gitignored)
interest/<ROOM>.json   hands raised for the offer (gitignored, survives RESET)
offers/                offer artwork uploaded in setup (gitignored)
rooms_state.json       live room state, so a restart doesn't lose the night
```
