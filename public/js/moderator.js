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
  $("#again-btn").addEventListener("click", () => { Live.send("askAgain"); UI.toast("Same poll, round two"); });
  // one exclusive set: there is one projector, so one screen at a time
  [["join-btn", "join"],
   ["stats-btn", "stats"], ["holding-btn", "holding"], ["explainer-btn", "explainer"],
   ["offer-btn", "offer"], ["donate-btn", "donate"]].forEach(([id, which]) => {
    $("#" + id).addEventListener("click", () => Live.send("showScreen", { which }));
  });
  $("#start-btn").addEventListener("click", () => Live.send("startEvent"));
  $("#undo-btn").addEventListener("click", () => {
    Live.send("undoRemove");
    $("#undo-bar").hidden = true;
  });
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

  // What the room has actually put on the wall. No filter catches everything,
  // so the crew gets a one-tap way to take a word down.
  function paintWordCloud(st) {
    const cloud = st.wordcloud;
    const on = st.mode === "wordcloud" && cloud;
    $("#wc-panel").hidden = !on;
    if (!on) return;
    const words = (cloud.words || []).slice().sort((a, b) => b.weight - a.weight);
    $("#wc-count").textContent = words.length;
    const host = $("#wc-list");
    host.innerHTML = "";
    if (!words.length) {
      const empty = document.createElement("div");
      empty.className = "wc-empty";
      empty.textContent = "Nothing submitted yet.";
      host.appendChild(empty);
      return;
    }
    words.forEach((w) => {
      const b = document.createElement("button");
      b.className = "wc-word";
      b.type = "button";
      b.title = "Remove \u201c" + w.text + "\u201d from the screen";
      // textContent throughout: audience text never becomes markup
      const label = document.createElement("span");
      label.textContent = w.text;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = w.weight;
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "\u00d7";
      b.append(label, n, x);
      b.addEventListener("click", () => Live.send("removeWord", { word: w.text }));
      host.appendChild(b);
    });
  }

  // Who is in the room tonight. Aggregate only — the moderator sees the shape
  // of the room, not a list of who said what about themselves.
  function paintRoomStats(st) {
    const rs = st.roomStats;
    if (!rs) return;
    $("#sr-count").textContent = rs.checkedIn;
    const any = rs.checkedIn > 0;
    $("#sr-note").hidden = any;
    $("#sr-occ-block").hidden = !any || !rs.occupations.length;
    const vibes = rs.vibes.filter((v) => v.count > 0);
    $("#sr-vibe-block").hidden = !vibes.length;

    const top = Math.max(1, ...rs.occupations.map((o) => o.count));
    const host = $("#sr-occ");
    host.innerHTML = "";
    rs.occupations.forEach((o) => {
      const row = document.createElement("div");
      row.className = "sr-bar";
      const fill = document.createElement("div");
      fill.className = "fill";
      fill.style.width = Math.round((o.count / top) * 100) + "%";
      const t = document.createElement("span");
      t.className = "t";
      t.textContent = o.label;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = o.count;
      row.append(fill, t, n);
      host.appendChild(row);
    });

    const vhost = $("#sr-vibes");
    vhost.innerHTML = "";
    vibes.sort((a, b) => b.count - a.count).forEach((v) => {
      const chip = document.createElement("div");
      chip.className = "sr-vibe";
      const e = document.createElement("span");
      e.className = "e";
      e.textContent = v.char;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = v.count;
      const l = document.createElement("span");
      l.className = "l";
      l.textContent = v.label;
      chip.append(e, n, l);
      vhost.appendChild(chip);
    });

  }

  function render(st) {
    if (!st) return;
    paintWordCloud(st);
    paintRoomStats(st);
    $("#mod-brand").innerHTML = st.brand + " <em>LIVE</em>";
    // What the room is actually looking at. A takeover covers everything, so
    // saying "DISCUSSION" while the holding loop is up describes the moderator's
    // intent rather than the audience's experience — and that is the gap where
    // you talk over a screen nobody can see past.
    const SCREEN_LABEL = {
      join: "JOIN CODE", holding: "HOLDING SCREEN", explainer: "HOW IT WORKS",
      stats: "STATE OF THE ROOM", offer: "THE OFFER", donate: "THE ASK",
    };
    const onWall = SCREEN_LABEL[st.screen];
    const label = $("#mode-label");
    if (onWall) {
      label.textContent = "ON THE WALL · " + onWall;
      label.classList.add("takeover");
      label.classList.remove("hidden-state");
    } else {
      label.textContent = (MODE_LABEL[st.mode] || "DISCUSSION") + (st.revealed ? "" : " · HIDDEN");
      label.classList.remove("takeover");
      label.classList.toggle("hidden-state", !st.revealed);
    }
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
    // asking again only makes sense once the room has seen the first result
    $("#again-btn").disabled = !st.rerunnable;
    $("#again-btn").querySelector(".ql").textContent =
      st.poll.round > 1 ? "Round " + st.poll.round : "Ask Again";

    // Every screen button reads the same field, so the armed one is always the
    // one actually on the wall — they cannot disagree.
    const promos = st.promos || {};
    // Each says why it can't be pressed in its own words — a derived string
    // produced "No State of the Room Set", which explains nothing.
    [["join", "Join Code", "Hide Join Code", true, ""],
     ["holding", "Holding Screen", "Hide Holding", true, ""],
     ["explainer", "How It Works", "Hide Explainer", true, ""],
     ["stats", "State of the Room", "Hide the Room",
      (st.roomStats || {}).checkedIn > 0, "Nobody Checked In Yet"],
     ["offer", "Show Offer", "Hide Offer", Boolean(promos.offer), "No Offer Set"],
     ["donate", "Show Donate", "Hide Donate", Boolean(promos.donate), "No Ask Set"]]
      .forEach(([which, label, hide, ready, why]) => {
        const btn = $("#" + which + "-btn");
        const on = st.screen === which;
        btn.disabled = !ready && !on;
        btn.classList.toggle("armed", on);
        btn.querySelector(".ql").textContent = on ? hide : (ready ? label : why);
      });

    // Ending the night is the only button here that cannot be undone by pressing
  // it again, so it asks first. Everything else on this page is a toggle.
  let endArmed = false;
  $("#endnight-btn").addEventListener("click", async () => {
    const btn = $("#endnight-btn"), lab = btn.querySelector(".ql");
    if (!endArmed) {
      endArmed = true;
      btn.classList.add("armed");
      lab.textContent = "Tap again to end";
      setTimeout(() => {
        if (!endArmed) return;
        endArmed = false;
        btn.classList.remove("armed");
        lab.textContent = "End the Event";
      }, 6000);
      return;
    }
    endArmed = false;
    btn.classList.remove("armed");
    lab.textContent = "Ending…";
    btn.disabled = true;
    await fetch("/api/rooms/" + encodeURIComponent(Live.room()), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ closed: true }),
    });
    lab.textContent = "Event Ended";
  });

  // the undo offer, for as long as the server will still honour it
    $("#undo-bar").hidden = !st.undoable;

    // the START bar is only there until the night begins
    $("#start-bar").hidden = Boolean(st.started) || st.exists === false;
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
    renderQuestions(st);
  }

  // Questions arrive already ranked by votes. PUT UP sends one to the big
  // screen; DONE greys it out so the moderator can see what's been covered.
  // Who this person is, and a button to put them on the big screen. Profiles
  // only ever reach the projector through this deliberate action.
  function profileCard(st, pid, p) {
    const live = st.featuredProfilePid === pid;
    const card = document.createElement("div");
    card.className = "who" + (live ? " live" : "");

    const face = document.createElement("div");
    face.className = "who-face";
    if (p.avatar) face.style.backgroundImage = "url(/avatars/" + p.avatar + ")";
    else face.textContent = p.initials || "?";

    const txt = document.createElement("div");
    txt.className = "who-txt";
    if (p.fact) {
      const fact = document.createElement("div");
      fact.className = "fact";
      fact.textContent = "“" + p.fact + "”";
      txt.appendChild(fact);
    }

    const btn = document.createElement("button");
    btn.className = "mq-btn" + (live ? " on" : "");
    btn.textContent = live ? "ON SCREEN" : "SHOW WHO";
    btn.title = "Put this person on the projector";
    btn.addEventListener("click", () => Live.send("featureProfile", { target: pid }));

    // clears the name, occupation, fact and photo outright — for when what
    // someone put in their profile shouldn't be anywhere near the big screen
    const drop = document.createElement("button");
    drop.className = "mq-btn drop";
    drop.textContent = "✕";
    drop.title = "Delete this person's profile and photo";
    drop.addEventListener("click", () => {
      Live.send("removeProfile", { target: pid });
      UI.toast("Profile removed");
    });

    card.append(face, txt, btn, drop);
    return card;
  }

  function renderQuestions(st) {
    const questions = st.questions || [];
    $("#qa-count").textContent = questions.length;

    const list = $("#mod-qa-list");
    const key = questions.map((q) => q.id + ":" + q.votes + ":" + q.answered).join("|")
      + "|" + (st.featuredQuestion || "") + "|" + (st.featuredProfilePid || "")
      + "|" + Object.keys(st.profiles || {}).length;
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    list.innerHTML = "";

    if (!questions.length) {
      const empty = document.createElement("div");
      empty.className = "qa-empty";
      empty.textContent = "Nothing asked yet.";
      list.appendChild(empty);
      return;
    }

    questions.forEach((q) => {
      const live = st.featuredQuestion === q.id;
      const row = document.createElement("div");
      row.className = "mq" + (q.answered ? " answered" : "") + (live ? " live" : "");

      const votes = document.createElement("div");
      votes.className = "mq-votes";
      votes.innerHTML = '<span class="ar">▲</span><span class="n">' + q.votes + "</span>";

      const body = document.createElement("div");
      body.className = "mq-body";
      body.innerHTML = '<div class="qt"></div><div class="qm"></div>';
      body.querySelector(".qt").textContent = q.text;
      const profile = (st.profiles || {})[q.pid];
      body.querySelector(".qm").textContent =
        q.name + (profile && profile.occupation ? " · " + profile.occupation : "") +
        " · " + UI.ago(q.at) + (q.answered ? " · ANSWERED" : "");
      if (profile) body.appendChild(profileCard(st, q.pid, profile));

      const acts = document.createElement("div");
      acts.className = "mq-acts";
      const feature = document.createElement("button");
      feature.className = "mq-btn" + (live ? " on" : "");
      feature.textContent = live ? "ON SCREEN" : "PUT UP";
      feature.title = "Show this question on the projector";
      feature.addEventListener("click", () => Live.send("featureQuestion", { id: q.id }));
      const done = document.createElement("button");
      done.className = "mq-btn";
      done.textContent = q.answered ? "REOPEN" : "DONE";
      done.addEventListener("click", () => Live.send("answerQuestion", { id: q.id }));

      // one click, no confirm: when something needs to come off the screen it
      // needs to come off now
      const drop = document.createElement("button");
      drop.className = "mq-btn drop";
      drop.textContent = "REMOVE";
      drop.title = "Delete this question — it disappears from phones and the recap";
      drop.addEventListener("click", () => {
        Live.send("removeQuestion", { id: q.id });
        UI.toast("Question removed");
      });
      acts.append(feature, done, drop);

      row.append(votes, body, acts);
      list.appendChild(row);
    });
  }

  function renderChallenges(st) {
    const list = $("#ch-list");
    list.innerHTML = "";
    st.challenges.slice(0, 8).forEach((c) => {
      const invited = st.invited.includes(c.id);
      const profile = (st.profiles || {})[c.pid];
      const el = document.createElement("div");
      el.className = "ch-item" + (invited ? " invited" : "");

      const av = document.createElement("div");
      av.className = "av";
      if (profile && profile.avatar) av.style.backgroundImage = "url(/avatars/" + profile.avatar + ")";
      else av.textContent = c.initials;

      const body = document.createElement("div");
      body.style.cssText = "flex:1;min-width:0";
      body.innerHTML =
        '<div class="nm"><span>' + escapeHtml(c.name) +
          (invited ? ' <span class="tag-invited">· INVITED</span>' : '') +
        '</span><span class="t">' + UI.ago(c.at) + '</span></div>' +
        '<div class="tx">' + escapeHtml(c.text) + '</div>';

      // Putting the words on the wall is a deliberate act: the projector shows
      // that someone pushed back the moment they do, but never what they wrote
      // until it has been read here.
      const up = document.createElement("button");
      const onWall = st.featuredChallenge === c.id;
      up.className = "ch-up" + (onWall ? " on" : "");
      up.textContent = onWall ? "ON THE WALL" : "PUT UP";
      up.title = onWall
        ? "Take these words off the projector"
        : "Show these words on the projector";
      up.addEventListener("click", () => Live.send("featureChallenge", { id: c.id }));

      const drop = document.createElement("button");
      drop.className = "ch-drop";
      drop.textContent = "✕";
      drop.title = "Remove this challenge";
      drop.addEventListener("click", () => {
        Live.send("removeChallenge", { id: c.id });
        UI.toast("Challenge removed");
      });
      body.querySelector(".nm").append(up, drop);

      if (profile) body.appendChild(profileCard(st, c.pid, profile));

      el.append(av, body);
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
