# Event night runbook

What to do when something goes wrong, written after actually breaking each of
these on purpose rather than guessing. Every finding below was measured, and
the app was changed where the answer wasn't good enough.

Keep this open on your phone during the event.

---

## Before the doors open

```bash
python3 test_smoke.py          # ~0.3s, must say "0 failed"
```

Then, in order:

1. Open `/setup`, check the event is right and the offer says what you want.
2. Open the room. Note the code — and check it is the one marked **TONIGHT**
   in setup, which is where the bare address sends anyone who types it.
3. Open `/projector` on the screen, press `F11` (Windows) or `⌃⌘F` (Mac).
4. Open `/moderator` on your laptop. Confirm the room code matches.
5. Join from your own phone on **mobile data, not the venue Wi-Fi** — that
   proves the public address works from outside the building.
5b. Print the table cards and door poster from `/print?room=CODE`. Plain white
   paper. They matter more than the projector: most people arrive late, looking
   at a phone, facing away from the screen.
6. Press **RESET** in setup — which also puts the room back into the lobby. This wipes your rehearsal and the demo's seeded
   numbers. It does **not** touch debrief sign-ups, offer leads, or the archive
   — the rehearsal is marked as a rehearsal and kept out of the reports.

---

## The drills, and what actually happens

### The app is redeployed or restarted mid-event
**Measured: nothing is lost.** A redeploy sends `SIGTERM`; the app catches it,
saves, and exits. Topic position, every tally, the challenge queue and the
check-ins all come back exactly as they were.

### The machine is killed outright — out of memory, host failure
**Measured: up to 5 seconds of activity is lost.** State is written every 5
seconds, so the last few votes or a challenge posted in that window can vanish.
Everything older survives. Debrief sign-ups and offer leads are written the
instant they happen and are never in that window; the archive's queue flushes
every second, so at most a second of responses is missing from the record.

Nothing to do — it comes back on its own. Carry on from where the screen says
you are, and if a challenge posted seconds before the crash is missing, ask
that person to send it again.

### Is the archive actually recording?

`/api/archive` (signed in as crew) answers with what it holds — sessions,
attendees, responses. Check it once during the first topic. If `responses` is
climbing, the night is being written down.

### After the event — where everything is
`/crm` (**The Rooms**) holds it: everyone who came, everything they said, and a
report per evening. Export from there before you follow anyone up.

### The state file is corrupt
**Measured: it falls back to the previous autosave and keeps going.** Every save
keeps the last good copy as `rooms_state.json.bak`; a corrupt live file costs
you one autosave, not the night. The log says `Restored N room(s) from the
backup copy`.

### The data volume fills up or goes read-only
**Measured: the event carries on. Votes, polls and the projector are all in
memory and unaffected.** What stops is saving: sign-ups and offer leads are
refused *with an honest error on the phone* rather than a tick. If you see
guests reporting "that didn't save", the volume is the problem — take the
sign-ups on paper for the rest of the night.

Check with: `~/.fly/bin/flyctl ssh console -a whatsnext -C "df -h /data"`

### A phone loses signal, or locks itself
**Measured: it reconnects on its own and catches up.** The phone shows
`RECONNECTING…`, and when the server returns it lands on whatever is live *now*
— including a poll launched while it was away — without going back to the join
screen. It can vote immediately. Tested by killing the server under a live
phone and restarting it.

Tell anyone who asks: **do nothing, it comes back.** Reloading is safe too.

### The projector browser crashes or the laptop reboots
Reopen `/projector?room=CODE`. It rejoins whatever is live, including the
holding loop if that was up. Nothing on the projector holds any state of its own.

### What does the debrief actually say?
**✉ preview** on the room's row in setup shows the exact email, offer and all.

### Is the debrief able to send?
The setup page says so in a line above the room list. **SEND DEBRIEF** itself
only appears on a room that already has sign-ups — no sign-ups, no button — so
its absence tells you nothing either way.

### Starting the night
Everyone who checks in sits in the lobby — the room filling up — and the wall
holds the join code. Press **START THE EVENT** when you're ready: the wall goes
to the holding loop, with the join QR still on it, which is your minute to walk
to the front. Take it down when you want the first topic. If you forget the
button and simply launch something, that starts the event too.

**HOW IT WORKS** is worth putting up once while people settle — seven animated
slides, 20 seconds each, about two and a half minutes for the full loop. Put it
up as the room fills and take it down when you're ready to start.

### Putting a screen up
**HOLDING**, **STATE OF THE ROOM**, **SHOW OFFER** and **SHOW DONATE** are one
exclusive set: one projector, one screen. Pressing another swaps it; pressing
the lit one takes it down. A button reading "No Offer Set" means that event
hasn't been given one in setup.

### You need the room off the screen *right now*
**HOLDING SCREEN** in the control room. It covers everything — polls, results,
the offer, the join code — with the video loop. Press again to go back to
exactly what was there.

### Something offensive is on the word cloud
The control room lists every word on the wall while a cloud is live. Tap it. It
goes immediately and cannot be re-submitted. (Profanity is filtered on the way
in and each phone only gets three words, but no filter catches everything.)

### Someone has challenged and you want the room to read it
The wall already flashed their name when they posted. **PUT UP** on that
challenge in the control room shows the words; pressing it again takes them
down. Only one thing sits on the wall at a time, so this replaces a featured
question and vice versa.

### A question or challenge needs to go
Every question and challenge has **REMOVE** — and an **UNDO** appears for twelve
seconds afterwards if you hit the wrong one.

### Someone wants their data deleted, there and then
Their phone: profile → **Delete my details** (two taps). During the event this
also removes their answers from the archive — the phone still knows its own
token, so the row can be found. After the event it can't be, which is exactly
what makes those answers anonymous.

For an email address, the unsubscribe link in any debrief email offers deletion
of the contact record. Their answers aren't affected and don't need to be:
nothing links them to a person.

---

## What I would still not risk

- **One machine, one region.** If Fly's London region has an outage, the event
  has no app. There is no second machine and no failover. For a high-stakes
  event, rehearse the no-app version of your run of show.
- **The venue's Wi-Fi.** Nothing in this app can fix a room where 200 phones
  can't reach the internet. Ask the venue about the guest network's client
  limit before the day.
- **Sending the debrief.** It needs SMTP configured. If it isn't, the sign-ups
  are still safely on disk and downloadable from `/setup` — you can send them
  from your own mail later.

---

## Useful commands

```bash
~/.fly/bin/flyctl status -a whatsnext          # is it up, which version
```

```bash
~/.fly/bin/flyctl logs -a whatsnext            # what it is saying
```

```bash
~/.fly/bin/flyctl ssh console -a whatsnext -C "ls -la /data"
```

Take a copy of the archive off the machine:

```bash
~/.fly/bin/flyctl ssh sftp get /data/crm.sqlite3 -a whatsnext
```

```bash
~/.fly/bin/flyctl machine restart -a whatsnext # last resort; state survives
```
