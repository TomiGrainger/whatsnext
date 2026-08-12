// Shared real-time client for all three surfaces.
// - subscribes to the server snapshot via Server-Sent Events
// - sends actions via POST /api/action
window.Live = (function () {
  const listeners = [];
  let state = null;
  let es = null;
  let role = "audience";

  // room code from ?room= (default WN25), sanitized to match the server
  function sanitize(c) { return (c || "").replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8) || "WN25"; }
  let roomCode = sanitize(new URLSearchParams(location.search).get("room"));

  // stable per-device participant id
  function pid() {
    let p = localStorage.getItem("upgrade_pid");
    if (!p) { p = "p_" + Math.random().toString(36).slice(2, 10); localStorage.setItem("upgrade_pid", p); }
    return p;
  }

  function open() {
    if (es) es.close();
    es = new EventSource("/events?role=" + encodeURIComponent(role) + "&room=" + encodeURIComponent(roomCode));
    es.onmessage = (ev) => {
      try {
        state = JSON.parse(ev.data);
        listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
      } catch (e) { /* heartbeat / non-json */ }
    };
    es.onerror = () => { /* EventSource auto-reconnects */ };
  }

  function connect(r) { role = r || "audience"; open(); }

  // switch rooms live (used by the audience join screen)
  function setRoom(code) {
    const c = sanitize(code);
    if (c === roomCode && es) return;
    roomCode = c; state = null; open();
  }

  async function send(type, extra) {
    const body = Object.assign({ type, pid: pid(), room: roomCode }, extra || {});
    try {
      await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) { console.error("action failed", e); }
  }

  function onState(fn) { listeners.push(fn); if (state) fn(state); }
  function get() { return state; }
  function room() { return roomCode; }

  return { connect, send, onState, get, pid, setRoom, room };
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
