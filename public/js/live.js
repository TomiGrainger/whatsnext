// Shared real-time client for all three surfaces.
// - subscribes to the server snapshot via Server-Sent Events
// - sends actions via POST /api/action
window.Live = (function () {
  const listeners = [];
  let state = null;
  let es = null;
  let role = "audience";

  // The room comes from the path (/WN25 — what a QR carries and what you can
  // say from a stage) or from ?room=, which still works for anything older.
  function sanitize(c) { return (c || "").replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8) || "WN25"; }
  const fromPath = location.pathname.replace(/^\/+|\/+$/g, "");
  const codeInUrl = /^[a-zA-Z0-9]{1,8}$/.test(fromPath)
    ? fromPath : new URLSearchParams(location.search).get("room");
  let roomCode = sanitize(codeInUrl);
  // whether the room was named for them — the QR did it, so don't ask again
  const roomWasGiven = Boolean(codeInUrl);

  // stable per-device participant id
  function pid() {
    let p = localStorage.getItem("upgrade_pid");
    if (!p) { p = "p_" + Math.random().toString(36).slice(2, 10); localStorage.setItem("upgrade_pid", p); }
    return p;
  }

  // ---- connection health -------------------------------------------------
  // A phone that loses wifi mid-event must come back on its own. EventSource
  // reconnects by itself, but it can also sit in a half-open socket that never
  // errors, so a watchdog reopens the stream if the server goes quiet. The
  // server pings every 15s and pushes a snapshot every second while live.
  const QUIET_MS = 20000;
  const statusListeners = [];
  const burstListeners = [];
  let online = false;
  let lastMessage = 0;
  let watchdog = null;

  function setOnline(v) {
    if (online === v) return;
    online = v;
    statusListeners.forEach((fn) => { try { fn(online); } catch (e) { console.error(e); } });
  }

  function open() {
    if (es) es.close();
    es = new EventSource("/events?role=" + encodeURIComponent(role) + "&room=" + encodeURIComponent(roomCode));
    lastMessage = Date.now();
    es.onopen = () => { lastMessage = Date.now(); setOnline(true); };
    es.onmessage = (ev) => {
      lastMessage = Date.now();
      setOnline(true);
      try {
        const msg = JSON.parse(ev.data);
        // most messages are the room snapshot; tagged ones are one-off blips
        if (msg && msg.t === "burst") {
          burstListeners.forEach((fn) => { try { fn(msg.emoji); } catch (e) { console.error(e); } });
          return;
        }
        state = msg;
        listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
      } catch (e) { /* heartbeat / non-json */ }
    };
    es.onerror = () => setOnline(false);
  }

  function startWatchdog() {
    if (watchdog) return;
    watchdog = setInterval(() => {
      if (document.hidden) return;         // a backgrounded tab is expected to be quiet
      if (Date.now() - lastMessage > QUIET_MS) {
        setOnline(false);
        open();                            // silence for too long: rebuild the stream
      }
    }, 5000);
    // coming back from sleep / screen-lock / tab switch: check in immediately
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && Date.now() - lastMessage > QUIET_MS) open();
    });
    window.addEventListener("online", () => open());
  }

  function connect(r) { role = r || "audience"; open(); startWatchdog(); }
  function onStatus(fn) { statusListeners.push(fn); fn(online); }
  function onBurst(fn) { burstListeners.push(fn); }
  function isOnline() { return online; }

  // Reactions fire while a finger is held down, so they skip the retry queue —
  // a dropped one is simply a lost blip, and retrying would pile them up.
  function burst(emoji) {
    fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "burst", emoji, pid: pid(), room: roomCode }),
      keepalive: true,
    }).catch(() => {});
  }

  // switch rooms live (used by the audience join screen)
  function setRoom(code) {
    const c = sanitize(code);
    if (c === roomCode && es) return;
    roomCode = c; state = null; open();
  }

  // Actions retry briefly: a tap that lands in a wifi dropout should still
  // count once the phone is back, rather than being silently lost.
  async function send(type, extra) {
    const body = Object.assign({ type, pid: pid(), room: roomCode }, extra || {});
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const r = await fetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        // hand back the parsed body so a caller can read a hint (still truthy)
        if (r.ok) return await r.json().catch(() => true);
        // only the crew surfaces have a login to go back to — never bounce a
        // guest's phone to a passcode screen
        if (r.status === 401 && role === "moderator") {
          location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
          return false;
        }
        if (r.status === 401 || r.status === 404) return false;
      } catch (e) { /* offline — fall through to the retry */ }
      setOnline(false);
      await new Promise((res) => setTimeout(res, 400 * (attempt + 1)));
    }
    return false;
  }

  function onState(fn) { listeners.push(fn); if (state) fn(state); }
  function get() { return state; }
  function room() { return roomCode; }

  return { connect, send, burst, onState, onStatus, onBurst, isOnline, get, pid, setRoom, room,
           roomWasGiven: () => roomWasGiven };
})();

// small helpers shared by pages
window.UI = {
  fmtTime(sec) {
    sec = Math.max(0, sec | 0);
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + String(s).padStart(2, "0");
  },
  ago(ts) {
    const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (d < 60) return d + "s ago";
    const m = Math.floor(d / 60);
    if (m < 60) return m + "m ago";
    return Math.floor(m / 60) + "h ago";
  },
  toast(msg) {
    let t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 1600);
  },
};
