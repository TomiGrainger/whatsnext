// Moderator Control Room controller.
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const MODE_LABEL = {
    discussion: "DISCUSSION", poll: "POLL", wordcloud: "WORD CLOUD",
    emoji: "EMOJI REACTIONS", slider: "SLIDER", ranking: "RANKING", results: "SHOWING RESULTS",
  };
  const KIND_ICON = {
    poll: "📊", wordcloud: "☁️", emoji: "🙂", slider: "🎚", ranking: "↕",
  };

  // topic flow
  $("#extend-btn").addEventListener("click", () => { Live.send("extendTime"); UI.toast("+30s added"); });
  $("#next-btn").addEventListener("click", () => Live.send("nextTopic"));
  $("#next-btn-2").addEventListener("click", () => Live.send("nextTopic"));
  $("#prev-btn").addEventListener("click", () => Live.send("prevTopic"));
  $("#pause-btn").addEventListener("click", () => Live.send("togglePause"));
  $("#results-btn").addEventListener("click", () => { Live.send("showResults"); UI.toast("Showing results"); });
  $("#end-btn").addEventListener("click", () => { Live.send("showResults"); UI.toast("Showing results"); });
  $("#discussion-btn").addEventListener("click", () => { Live.send("backToDiscussion"); UI.toast("Back to discussion"); });
  $("#reveal-btn").addEventListener("click", () => { Live.send("reveal"); UI.toast("Results revealed"); });
  $("#invite-btn").addEventListener("click", () => { Live.send("inviteTop"); UI.toast("Invited to speak"); });
  // sidebar quick-jumps
  $$('.nav a[data-jump]').forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); document.getElementById(a.dataset.jump).scrollIntoView({ behavior: "smooth" });
  }));

  // One button per interaction this topic was set up with, in the same order as
  // the setup page — plus the discussion the topic always opens on.
  function renderRunRow(st) {
    const row = $("#run-row");
    const key = st.topicIndex + ":" + st.interactions.map((i) => i.kind).join(",");
    if (row.dataset.key !== key) {
      row.dataset.key = key;
      row.innerHTML = "";

      const disc = document.createElement("button");
      disc.className = "run-btn discussion";
      disc.dataset.role = "discussion";
      disc.innerHTML = '<span class="ri">💬</span><span class="rl">Discussion</span>' +
        '<span class="rt">no timer</span>';
      disc.addEventListener("click", () => Live.send("backToDiscussion"));
      row.appendChild(disc);

      st.interactions.forEach((it) => {
        const b = document.createElement("button");
        b.className = "run-btn";
        b.dataset.index = it.index;
        b.innerHTML = '<span class="ri">' + (KIND_ICON[it.kind] || "◆") + '</span>' +
          '<span class="rl"></span><span class="rt">' + it.duration + 's</span>';
        b.querySelector(".rl").textContent = it.question || it.kind;
        b.addEventListener("click", () => {
          Live.send("launchInteraction", { index: it.index });
          UI.toast((it.question || it.kind) + " launched");
        });
        row.appendChild(b);
      });

      if (!st.interactions.length) {
        const none = document.createElement("div");
        none.className = "run-none";
        none.textContent = "Discussion only — no interactions set up for this topic.";
        row.appendChild(none);
      }
    }

    $$("#run-row .run-btn").forEach((b) => {
      const live = b.dataset.role === "discussion"
        ? st.activeInteraction === null && st.mode !== "results"
        : Number(b.dataset.index) === st.activeInteraction && st.mode !== "results";
      b.classList.toggle("live", live);
    });
  }

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
    $("#mod-brand").innerHTML = st.brand + " <em>LIVE</em>";
    $("#mode-label").textContent = (MODE_LABEL[st.mode] || "DISCUSSION") + (st.revealed ? "" : " · HIDDEN");
    $("#mode-label").classList.toggle("hidden-state", !st.revealed);
    const missing = st.exists === false;
    $("#closed-banner").hidden = !missing && !st.closed;
    $("#closed-banner").textContent = missing
      ? "Room " + st.code + " isn't open. Open it from the setup page to start driving the event."
      : "This room is switched off — phones can't vote. Turn it back on from the setup page.";
    document.body.classList.toggle("room-closed", Boolean(st.closed));
    if (missing) {
      $("#topic-q").textContent = "—";
      $("#run-row").innerHTML = "";
      $("#run-row").dataset.key = "";
      return;
    }

    $("#in-room").textContent = st.inRoom;
    // a discussion has no countdown — it holds until the moderator moves on
    $("#time-rem").textContent = st.timed ? UI.fmtTime(st.timeRemaining) : "—";
    $("#time-lab").textContent = st.timed ? "REMAINING" : "ON SCREEN";
    $("#pause-btn").innerHTML = st.paused ? "▶ RESUME" : "❚❚ PAUSE";
    $("#pause-btn").disabled = !st.timed;
    $("#extend-btn").disabled = !st.timed;
    $("#discussion-btn").disabled = st.activeInteraction === null && st.mode !== "results";
    // the crew always sees real figures; this button is what lets the room see them
    $("#reveal-btn").disabled = !st.revealable;
    $("#reveal-btn").classList.toggle("armed", Boolean(st.revealable));
    $("#reveal-btn").querySelector(".ql").textContent =
      st.revealable ? "Reveal Results" : (st.revealed ? "Results Showing" : "Reveal Results");
    $("#prev-btn").disabled = st.topicIndex === 0;
    $("#next-btn").disabled = st.topicIndex >= st.topicCount - 1;
    $("#next-btn-2").disabled = st.topicIndex >= st.topicCount - 1;

    $("#topic-n").textContent = st.topicIndex + 1;
    $("#topic-t").textContent = st.topicCount;
    $("#topic-q").textContent = st.topic;
    renderRunRow(st);

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
