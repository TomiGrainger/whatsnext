// Moderator Control Room controller.
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const MODE_LABEL = {
    discussion: "DISCUSSION MODE", poll: "POLL MODE", wordcloud: "WORD CLOUD",
    emoji: "EMOJI REACTIONS", slider: "SLIDER", ranking: "RANKING", results: "SHOWING RESULTS",
  };

  // mode dropdown
  const menu = $("#mode-menu");
  $("#mode-btn").addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("open"); });
  document.addEventListener("click", () => menu.classList.remove("open"));
  menu.addEventListener("click", (e) => e.stopPropagation());
  $$("#mode-menu button").forEach((b) =>
    b.addEventListener("click", () => { Live.send("setMode", { mode: b.dataset.mode }); menu.classList.remove("open"); }));

  // quick actions
  $$(".qa[data-mode]").forEach((b) =>
    b.addEventListener("click", () => { Live.send("setMode", { mode: b.dataset.mode }); UI.toast(b.querySelector(".ql").textContent + " launched"); }));
  $("#extend-btn").addEventListener("click", () => { Live.send("extendTime"); UI.toast("+30s added"); });
  $("#next-btn").addEventListener("click", () => { Live.send("nextTopic"); UI.toast("Next topic"); });
  $("#pause-btn").addEventListener("click", () => Live.send("togglePause"));
  $("#end-btn").addEventListener("click", () => { Live.send("endTopic"); UI.toast("Topic ended"); });
  $("#invite-btn").addEventListener("click", () => { Live.send("inviteTop"); UI.toast("Invited to speak"); });
  // sidebar quick-jumps
  $$('.nav a[data-jump]').forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); document.getElementById(a.dataset.jump).scrollIntoView({ behavior: "smooth" });
  }));

  // donut: agree(white) / disagree(red) / unsure(dark)
  function drawDonut(st) {
    const s = st.sentiment;
    const segs = [
      { pct: s.agreePct, color: "#f5f3ef" },
      { pct: s.disagreePct, color: "#FF2D46" },
      { pct: s.unsurePct, color: "#26262b" },
    ];
    let off = 25; // start at top (12 o'clock) with pathLength 100
    let html = '<circle cx="18" cy="18" r="15.9" fill="none" stroke="#141417" stroke-width="4"/>';
    let cum = 0;
    segs.forEach((seg) => {
      const dash = Math.max(0, seg.pct);
      html += '<circle cx="18" cy="18" r="15.9" fill="none" stroke="' + seg.color +
        '" stroke-width="4" stroke-dasharray="' + dash + ' ' + (100 - dash) +
        '" stroke-dashoffset="' + (25 - cum) + '" pathlength="100" transform="rotate(0 18 18)"/>';
      cum += seg.pct;
    });
    $("#donut").innerHTML = html;
  }

  function drawSpark(st) {
    const h = st.sentimentHistory;
    if (!h || !h.length) return;
    const n = h.length;
    const x = (i) => (i / (n - 1)) * 100;
    const y = (v) => 38 - (v / 60) * 34; // scale ~0..60%
    const line = (key, color) => {
      const pts = h.map((p, i) => x(i).toFixed(1) + "," + y(p[key]).toFixed(1)).join(" ");
      return '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="1.6" stroke-linejoin="round"/>';
    };
    $("#spark").innerHTML = line("agree", "#f5f3ef") + line("disagree", "#FF2D46");
  }

  function render(st) {
    if (!st) return;
    $("#mode-label").textContent = MODE_LABEL[st.mode] || "DISCUSSION MODE";
    $$("#mode-menu button").forEach((b) => b.classList.toggle("on", b.dataset.mode === st.mode));

    $("#in-room").textContent = st.inRoom;
    $("#time-rem").textContent = UI.fmtTime(st.timeRemaining);
    $("#pause-btn").innerHTML = st.paused ? "▶ RESUME" : "❚❚ PAUSE";

    $("#topic-n").textContent = st.topicIndex + 1;
    $("#topic-t").textContent = st.topicCount;
    $("#topic-q").textContent = st.topic;

    $("#p-agree").textContent = st.sentiment.agreePct + "%";
    $("#p-disagree").textContent = st.sentiment.disagreePct + "%";
    $("#p-unsure").textContent = st.sentiment.unsurePct + "%";
    drawDonut(st);
    drawSpark(st);

    const wn = st.whatsNext;
    $("#wn-num").textContent = wn.votes;
    $("#wn-den").textContent = wn.threshold;
    $("#wn-bar").style.width = Math.min(100, (wn.votes / wn.threshold) * 100) + "%";
    $("#wn-hint").textContent = wn.unlocked ? "Ready — you can move on" : (wn.remaining + " more votes to unlock");

    $("#ch-count").textContent = st.challenges.length;
    $("#nav-ch").textContent = st.challenges.length;
    renderChallenges(st);
  }

  function renderChallenges(st) {
    const list = $("#ch-list");
    list.innerHTML = "";
    st.challenges.slice(0, 8).forEach((c) => {
      const invited = st.invited.includes(c.id);
      const el = document.createElement("div");
      el.className = "ch-item" + (invited ? " invited" : "");
      el.innerHTML =
        '<div class="av">' + c.initials + '</div>' +
        '<div style="flex:1;min-width:0">' +
          '<div class="nm"><span>' + escapeHtml(c.name) +
            (invited ? ' <span class="tag-invited">· INVITED</span>' : '') +
          '</span><span class="t">' + UI.ago(c.at) + '</span></div>' +
          '<div class="tx">' + escapeHtml(c.text) + '</div>' +
        '</div>';
      list.appendChild(el);
    });
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  $("#mod-room").innerHTML = "ROOM · <b>" + Live.room() + "</b>";
  Live.onState(render);
  Live.connect("moderator");
})();
