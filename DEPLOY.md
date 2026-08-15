# Deploying THE UPGRADE LIVE

Hosting it publicly means the audience joins over their own mobile data instead
of the venue's Wi-Fi — no captive portal, no "what's the network password?", and
the projector's QR works from anywhere in the room.

**Recommended host: [Fly.io](https://fly.io).** Reasons it suits this app:

- It runs a **single always-on machine**, which this app needs — room state lives
  in memory and in one file, so it must not be load-balanced across replicas.
- **Persistent volumes**, so `rooms_state.json` and `leads/` survive a redeploy.
- **Long-lived SSE connections** work fine through its proxy (the server sends a
  keepalive ping every 15s, well inside any idle timeout).
- Roughly **$3–5/month** for a 256MB machine plus a 1GB volume.

Render and Railway also work; the app is a plain Docker container. The one thing
to check on any host is that it won't sleep the instance or run more than one
copy. Render's free tier sleeps after inactivity and has no disk — don't use it
for a live event.

Everything below is yours to run — nothing here has been done for you.

---

## One-time setup (about 10 minutes)

### 1. Install the CLI and sign up

```bash
brew install flyctl
```

```bash
fly auth signup
```

A card is required even on the smallest plan. You'll be signed in afterwards.

### 2. Create the app

From the project directory. `fly.toml` is already written, so this only
registers the name — don't let it overwrite the config or deploy yet:

```bash
fly launch --no-deploy --copy-config --name the-upgrade-live
```

If that name is taken, pick another and change `app =` at the top of `fly.toml`
to match. If it asks to tweak settings, decline.

### 3. Create the volume for saved state

Same region as `primary_region` in `fly.toml`:

```bash
fly volumes create upgrade_data --size 1 --region lhr
```

### 4. Set the passcode and public URL

The passcode guards the moderator and setup pages. Pick your own:

```bash
fly secrets set PASSCODE=your-passcode-here
```

`PUBLIC_URL` is what the QR code sends phones to. Until you add a custom domain
it's `https://<your-app-name>.fly.dev`:

```bash
fly secrets set PUBLIC_URL=https://the-upgrade-live.fly.dev
```

Get this wrong and the QR will point somewhere phones can't reach, so it's worth
double-checking against what `fly deploy` prints.

### 5. Deploy

```bash
fly deploy
```

### 6. Check it

```bash
fly logs
```

You want a startup banner showing `Data dir: /data` and your public URLs. Then
open `https://<your-app>.fly.dev/setup`, enter the passcode, open a room, and
load the projector — the QR should show your public address, not a `192.168.x.x`
one. Scan it with a phone **on mobile data, with Wi-Fi off** to prove the point.

---

## Optional: your own domain

```bash
fly certs add debate.yourdomain.com
```

It prints the DNS records to add at your registrar (an A/AAAA record, or a CNAME
to `<your-app>.fly.dev`). Once the certificate is issued, update the public URL
so the QR uses the nice name:

```bash
fly secrets set PUBLIC_URL=https://debate.yourdomain.com
```

Setting a secret restarts the app, which is fine between events but will briefly
interrupt a live one.

---

## Running an event on it

Same as locally, just at the public URL:

1. `/setup` → sign in with the passcode → open a room code for tonight.
2. Put `/projector?room=CODE` on the big screen — it shows the QR.
3. Open `/moderator?room=CODE` on your laptop.
4. Rehearse, then **RESET** the room in setup before doors open.

Afterwards, download the debrief sign-ups from the ✉ link in setup, or:

```bash
fly ssh console -C "cat /data/leads/CODE.json"
```

## Things worth knowing

- **Keep it to one machine.** `fly scale count 1`. Two machines would each hold
  their own rooms in memory and the audience would land on whichever answered.
- **Deploying restarts the app.** Rooms, tallies, events and sign-ups all live on
  the volume and survive; the app saves on shutdown, so even the last few seconds
  are kept. The crew is signed out (sessions are in memory) and phones reconnect
  on their own. Still, don't deploy during an event.
- **Events live on the volume too.** An event you author through `/setup` on the
  deployed app persists across deploys, and a later deploy will not overwrite it
  even if a file of the same name ships in the image. The bundled events are
  copied onto the volume only when they aren't already there, so the demo event
  appears on first boot and your edits are never clobbered. The trade-off: an
  updated bundled event won't reach a volume that already has that file — delete
  it there (`fly ssh console -C "rm /data/events/NAME.json"`) and redeploy if you
  want the shipped version back.
- **Back up sign-ups** after each event; the volume is a single disk, not a
  managed database.

## Environment variables

| Variable | What it does | Default |
|---|---|---|
| `PASSCODE` | Crew passcode for `/moderator` and `/setup` | random 6 digits, printed at startup |
| `PUBLIC_URL` | Public base URL used by the QR and join links | detected LAN address |
| `DATA_DIR` | Where `rooms_state.json`, `leads/` and `events/` are written | the project directory |
| `PORT` | Port to listen on | `8000` (`8080` in Docker) |

Locally you need none of them — `python3 server.py` still works exactly as it did,
and the QR falls back to your Wi-Fi address.
