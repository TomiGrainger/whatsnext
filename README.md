# THE UPGRADE LIVE

A real-time audience-engagement platform for live debates — think Slido/Mentimeter,
built to the designs in [`Designs/`](Designs). Three synchronized surfaces share one
authoritative live room:

| Surface | URL | Purpose |
|---|---|---|
| **Audience** (mobile) | `/` | Join, react, challenge, vote — 8 interaction screens |
| **Moderator** (desktop) | `/moderator` | Control Room: sentiment, "what's next", challenge queue, launch interactions |
| **Projector** (big screen) | `/projector` | Room Display for the audience to watch |

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
- **Frontend** — buildless vanilla JS. All three pages share `public/js/live.js`
  (the SSE + fetch client) and `public/css/app.css` (the design system: Anton display
  type, Space Mono labels, the crimson-on-black palette).
- **State** is seeded to match the mockups exactly (sentiment 41/37/22, poll
  42/28/17/13, the emoji counts, the challenge queue, etc.) so every surface looks
  alive on first load; real votes then accumulate on top.

## The moderator drives the show

The moderator's **mode** (Discussion / Poll / Word Cloud / Emoji / Slider / Ranking /
Results) decides which screen every audience phone shows — switch it from the
`DISCUSSION MODE ▾` dropdown or the Quick Actions row, and every phone follows
instantly. Audience votes flow back the other way into the sentiment ring, the
"what's next" counter, and the challenge queue.

## Files

```
server.py              real-time server (stdlib only)
public/
  audience.html        mobile surface — 8 interaction screens
  moderator.html       control room dashboard
  projector.html       room display
  css/app.css          shared design system
  css/{audience,moderator,projector}.css
  js/live.js           shared SSE + action client
  js/{audience,moderator}.js
Designs/               the visual source of truth (do not redesign)
```
