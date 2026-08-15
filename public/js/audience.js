// Audience surface controller.
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  let joined = sessionStorage.getItem("upgrade_joined") === "1";
  let mySentiment = null;
  let myNextVoted = false;
  let myPoll = null;
  let mySlider = null;
  let sliderActive = false;
  let rankBuilt = false;
  let lastMode = null;

  const MODE_SCREEN = {
    discussion: "s-discussion", poll: "s-poll", wordcloud: "s-wordcloud",
    emoji: "s-emoji", slider: "s-slider", ranking: "s-ranking", results: "s-results",
  };

  // decorative "scan to join" tile
  $("#qr-tile").innerHTML =
    '<svg width="150" height="150" viewBox="0 0 100 100" aria-hidden="true">' +
    '<g fill="none" stroke="#151515" stroke-width="7">' +
    '<rect x="14" y="14" width="24" height="24" rx="4"/>' +
    '<rect x="62" y="14" width="24" height="24" rx="4"/>' +
    '<rect x="14" y="62" width="24" height="24" rx="4"/></g>' +
    '<rect x="42" y="42" width="20" height="20" rx="6" fill="#FF2D46"/>' +
    '<text x="52" y="57" font-family="Space Mono,monospace" font-size="15" font-weight="700" fill="#fff" text-anchor="middle">?</text>' +
    '</svg>';

  function showScreen(id) {
    $$(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
  }

  function updateCounter(st) {
    const onJoin = $(".screen.active").id === "s-join";
    $("#ph-counter").textContent = onJoin
      ? st.eventName
      : String(st.topicIndex + 1).padStart(2, "0") + " / " + String(st.topicCount).padStart(2, "0");
  }

  // ---- join ----
  const codeInput = $("#join-code");
  codeInput.value = Live.room();
  codeInput.addEventListener("input", () => {
    codeInput.value = codeInput.value.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
  });
  codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#join-btn").click(); });
  $("#join-btn").addEventListener("click", () => {
    const code = codeInput.value.trim() || "WN25";
    Live.setRoom(code);          // (re)connect to the chosen room
    joined = true;
    sessionStorage.setItem("upgrade_joined", "1");
    sessionStorage.setItem("upgrade_room", Live.room());
    Live.send("join");
    render(Live.get(), true);
    UI.toast("Joined room " + Live.room());
  });

  // ---- discussion: sentiment + fight + whats-next ----
  $$("#s-discussion .react[data-choice]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const choice = btn.dataset.choice;
      mySentiment = choice;
      updateSentimentSel();
      Live.send("sentiment", { choice });
      UI.toast(choice + " counted");
    });
  });
  $("#fight-btn").addEventListener("click", openSheet);
  $("#next-bar").addEventListener("click", () => {
    if (myNextVoted) return;
    myNextVoted = true;
    Live.send("whatsnext");
    UI.toast("Vote to move on counted");
  });

  function updateSentimentSel() {
    $$("#s-discussion .react[data-choice]").forEach((b) =>
      b.classList.toggle("sel", b.dataset.choice === mySentiment));
  }

  // ---- live reaction bursts ----
  // Hold a reaction down and it keeps flowing to the projector. The set is
  // fixed here and re-checked on the server, since these land on the big screen.
  const BURSTS = ["🔥", "👏", "❤️", "😂", "🤯", "🙌"];
  const BURST_EVERY = 160;   // ms between sends while held — fast enough to feel live

  (function buildBursts() {
    const row = $("#burst-row");
    BURSTS.forEach((emoji) => {
      const b = document.createElement("button");
      b.className = "burst";
      b.type = "button";
      b.textContent = emoji;
      b.setAttribute("aria-label", "React " + emoji);

      let timer = null;
      const fire = () => {
        Live.burst(emoji);
        const puff = document.createElement("span");
        puff.className = "burst-puff";
        puff.textContent = emoji;
        b.appendChild(puff);
        setTimeout(() => puff.remove(), 700);
      };
      const start = (e) => {
        e.preventDefault();
        if (timer) return;
        b.classList.add("held");
        fire();
        timer = setInterval(fire, BURST_EVERY);
      };
      const stop = () => {
        b.classList.remove("held");
        clearInterval(timer);
        timer = null;
      };
      b.addEventListener("pointerdown", start);
      b.addEventListener("pointerup", stop);
      b.addEventListener("pointercancel", stop);
      b.addEventListener("pointerleave", stop);
      row.appendChild(b);
    });
  })();

  // ---- Q&A ----
  // which questions this phone has upvoted; the server never sends voter ids
  const myVotes = new Set(JSON.parse(sessionStorage.getItem("upgrade_qvotes") || "[]"));
  const rememberVote = (id) => {
    myVotes.has(id) ? myVotes.delete(id) : myVotes.add(id);
    sessionStorage.setItem("upgrade_qvotes", JSON.stringify([...myVotes]));
  };

  function openQa() {
    $("#qa-sheet").classList.add("show");
    $("#qa-scrim").classList.add("show");
  }
  function closeQa() {
    $("#qa-sheet").classList.remove("show");
    $("#qa-scrim").classList.remove("show");
  }
  $("#qa-bar").addEventListener("click", openQa);
  $("#qa-x").addEventListener("click", closeQa);
  $("#qa-scrim").addEventListener("click", closeQa);

  function submitQuestion() {
    const text = $("#qa-text").value.trim();
    if (!text) { $("#qa-text").focus(); return; }
    Live.send("ask", { text });
    $("#qa-text").value = "";
    UI.toast("Question added");
  }
  $("#qa-send").addEventListener("click", submitQuestion);
  $("#qa-text").addEventListener("keydown", (e) => { if (e.key === "Enter") submitQuestion(); });

  function renderQa(st) {
    const questions = st.questions || [];
    $("#qa-count").textContent = questions.length ? "· " + questions.length : "";

    const list = $("#qa-list");
    const key = questions.map((q) => q.id + ":" + q.votes + ":" + q.answered).join("|");
    if (list.dataset.key === key) return;   // nothing moved; leave the DOM alone
    list.dataset.key = key;
    list.innerHTML = "";

    if (!questions.length) {
      const empty = document.createElement("div");
      empty.className = "qa-empty";
      empty.textContent = "No questions yet — be first.";
      list.appendChild(empty);
      return;
    }

    questions.forEach((q) => {
      const row = document.createElement("div");
      row.className = "qa-item" + (q.answered ? " answered" : "");
      const mine = myVotes.has(q.id);

      const vote = document.createElement("button");
      vote.className = "qa-vote" + (mine ? " on" : "");
      vote.innerHTML = '<span class="ar">▲</span><span class="n">' + q.votes + "</span>";
      vote.setAttribute("aria-label", (mine ? "Remove upvote from" : "Upvote") + ": " + q.text);
      vote.addEventListener("click", () => {
        rememberVote(q.id);
        vote.classList.toggle("on");
        Live.send("upvote", { id: q.id });
      });

      const body = document.createElement("div");
      body.className = "qa-body";
      body.innerHTML = '<div class="qt"></div><div class="qm"></div>';
      body.querySelector(".qt").textContent = q.text;
      body.querySelector(".qm").textContent = q.answered ? "ANSWERED · " + q.name : q.name;

      row.append(vote, body);
      list.appendChild(row);
    });
  }

  // ---- challenge sheet ----
  function openSheet() {
    $("#sheet").classList.add("show");
    $("#sheet-scrim").classList.add("show");
    setTimeout(() => $("#sheet-text").focus(), 250);
  }
  function closeSheet() {
    $("#sheet").classList.remove("show");
    $("#sheet-scrim").classList.remove("show");
  }
  $("#sheet-x").addEventListener("click", closeSheet);
  $("#sheet-cancel").addEventListener("click", closeSheet);
  $("#sheet-scrim").addEventListener("click", closeSheet);
  $("#sheet-submit").addEventListener("click", () => {
    const text = $("#sheet-text").value.trim();
    if (!text) { $("#sheet-text").focus(); return; }
    Live.send("challenge", { text, name: $("#sheet-name").value.trim() });
    $("#sheet-text").value = "";
    closeSheet();
    UI.toast("Challenge submitted");
  });

  // ---- word cloud submit ----
  function submitWord() {
    const w = $("#wc-input").value.trim();
    if (!w) return;
    Live.send("word", { word: w });
    $("#wc-input").value = "";
    UI.toast("Word added");
  }
  $("#wc-send").addEventListener("click", submitWord);
  $("#wc-input").addEventListener("keydown", (e) => { if (e.key === "Enter") submitWord(); });

  // ---- slider ----
  const slider = $("#slider-input");
  // The readout under the % comes from the topic's own labels: an explicit
  // resultLabel if the event defines one, otherwise whichever end it leans to.
  function sliderResLabel(v) {
    const s = Live.get() && Live.get().slider;
    if (!s) return "";
    if (s.resultLabel) return s.resultLabel;
    return v >= 50 ? s.rightLabel : s.leftLabel;
  }
  function paintSlider(v) {
    $("#slider-pct").textContent = v;
    $("#slider-res").textContent = sliderResLabel(v).toUpperCase();
    slider.style.background =
      "linear-gradient(90deg,var(--red) 0%,var(--red) " + v + "%,#2a2a2e " + v + "%,#2a2a2e 100%)";
  }
  slider.addEventListener("pointerdown", () => (sliderActive = true));
  slider.addEventListener("input", () => paintSlider(+slider.value));
  const endSlide = () => {
    if (!sliderActive) return;
    sliderActive = false;
    mySlider = +slider.value;
    Live.send("slider", { value: mySlider });
    UI.toast("Response recorded");
  };
  slider.addEventListener("pointerup", endSlide);
  slider.addEventListener("change", endSlide);

  // ---- emoji ----
  function buildEmoji(st) {
    const grid = $("#emoji-grid");
    if (grid.children.length !== st.emoji.reactions.length) {
      grid.innerHTML = "";
      st.emoji.reactions.forEach((r, i) => {
        const cell = document.createElement("button");
        cell.className = "emoji-cell";
        cell.innerHTML = '<div class="e">' + r.char + '</div><div class="c">' + r.count + "</div>";
        cell.addEventListener("click", (ev) => {
          Live.send("emoji", { id: r.id });
          const f = document.createElement("div");
          f.className = "float-plus"; f.textContent = "+1";
          const rect = cell.getBoundingClientRect();
          f.style.left = (ev.clientX - rect.left) + "px";
          f.style.top = "20px";
          cell.appendChild(f);
          setTimeout(() => f.remove(), 800);
        });
        grid.appendChild(cell);
      });
    } else {
      st.emoji.reactions.forEach((r, i) => {
        grid.children[i].querySelector(".c").textContent = r.count;
      });
    }
  }

  // ---- ranking: pointer-based sortable ----
  function buildRanking(st) {
    const list = $("#rank-list");
    list.innerHTML = "";
    st.ranking.items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "rank-item";
      row.dataset.id = it.id;
      row.innerHTML = '<div class="num"></div><div class="lab">' + it.label + '</div><div class="grip">⣿</div>';
      list.appendChild(row);
    });
    renumber();
    makeSortable(list);
    rankBuilt = true;
  }
  function renumber() {
    $$("#rank-list .rank-item").forEach((r, i) => (r.querySelector(".num").textContent = i + 1));
  }
  function makeSortable(list) {
    let dragEl = null, startY = 0, curY = 0;
    list.querySelectorAll(".rank-item").forEach((item) => {
      item.addEventListener("pointerdown", (e) => {
        dragEl = item; startY = e.clientY; curY = e.clientY;
        item.setPointerCapture(e.pointerId);
        item.classList.add("drag");
      });
      item.addEventListener("pointermove", (e) => {
        if (dragEl !== item) return;
        curY = e.clientY;
        item.style.transform = "translateY(" + (curY - startY) + "px)";
        const rows = $$("#rank-list .rank-item:not(.drag)");
        for (const other of rows) {
          const r = other.getBoundingClientRect();
          const mid = r.top + r.height / 2;
          if (curY < mid && item.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING) {
            list.insertBefore(item, other); reset(); break;
          }
          if (curY > mid && item.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_PRECEDING) {
            list.insertBefore(item, other.nextSibling); reset(); break;
          }
        }
        function reset() { startY = curY; item.style.transform = "translateY(0)"; }
      });
      const done = () => {
        if (dragEl !== item) return;
        item.style.transform = ""; item.classList.remove("drag"); dragEl = null;
        renumber();
        const order = $$("#rank-list .rank-item").map((r) => r.dataset.id);
        Live.send("ranking", { order });
        UI.toast("Ranking submitted");
      };
      item.addEventListener("pointerup", done);
      item.addEventListener("pointercancel", done);
    });
  }

  // ---- render ----
  function render(st, force) {
    if (!st) return;
    $("#join-code").textContent = st.code;
    $("#wordmark").innerHTML = st.brand + " <em>LIVE</em>";
    $("#eyebrow-brand").textContent = st.eventName;
    // a code nobody opened: say so plainly instead of a dead-looking screen
    const missing = st.exists === false;
    $("#nosuch-veil").hidden = !missing;
    $("#nosuch-code").textContent = st.code;
    $("#closed-veil").hidden = missing || !st.closed;
    $("#recap-link").href = "/recap?room=" + encodeURIComponent(st.code);
    if (missing) return;

    // choose screen
    const targetMode = st.mode;
    const screenId = joined ? (MODE_SCREEN[targetMode] || "s-discussion") : "s-join";
    const active = $(".screen.active");
    if (!active || active.id !== screenId || force) showScreen(screenId);
    updateCounter(st);

    // topic-dependent titles
    setQuestionTitle("#disc-q", st.topic, true);
    setQuestionTitle("#res-q", st.topic, true);

    renderQa(st);

    // discussion stats
    $("#st-agree").textContent = st.sentiment.agree;
    $("#st-disagree").textContent = st.sentiment.disagree;
    $("#st-unsure").textContent = st.sentiment.unsure;
    updateSentimentSel();
    const wn = st.whatsNext;
    const bar = $("#next-bar");
    bar.classList.toggle("unlocked", wn.unlocked);
    bar.innerHTML = wn.unlocked
      ? "✓ READY TO MOVE ON &nbsp; " + wn.votes + "/" + wn.threshold
      : "→ WHAT'S NEXT?&nbsp; " + wn.votes + "/" + wn.threshold;

    // poll
    $("#poll-q").textContent = st.poll.question.toUpperCase();
    renderPoll(st);
    const again = st.poll.round > 1;
    $("#poll-eyebrow").textContent = again ? "POLL · ROUND " + st.poll.round : "POLL";
    $("#poll-foot").textContent = again && !st.revealed
      ? "SAME QUESTION — HAS THE ROOM MOVED?"
      : st.revealed ? st.responses + " RESPONSES" : st.responses + " IN · RESULTS HIDDEN";

    // word cloud
    $("#wc-q").textContent = st.wordcloud.question.toUpperCase();
    renderCloud(st);
    $("#wc-foot").textContent = st.responses + " RESPONSES";

    // emoji
    setQuestionTitle("#emoji-q", st.emoji.question, true);
    buildEmoji(st);
    $("#emoji-foot").textContent = st.responses;

    // slider
    $("#slider-q").textContent = st.slider.question.toUpperCase();
    $("#slider-left").textContent = st.slider.leftLabel;
    $("#slider-right").textContent = st.slider.rightLabel;
    // while the room average is hidden the dial shows your own answer, so the
    // handle never gives the result away by drifting to the room's position
    if (!sliderActive) {
      const target = st.slider.avg === null ? (mySlider === null ? 50 : mySlider) : st.slider.avg;
      slider.value = target;
      paintSlider(target);
    }

    // ranking (build once per entry)
    $("#rank-q").textContent = st.ranking.question.toUpperCase();
    if (screenId === "s-ranking" && (!rankBuilt || force)) buildRanking(st);
    if (screenId !== "s-ranking") rankBuilt = false;
    $("#rank-foot").textContent = st.responses;

    // results
    $("#res-agree").textContent = st.sentiment.agreePct;
    $("#res-disagree").textContent = st.sentiment.disagreePct;
    $("#res-unsure").textContent = st.sentiment.unsurePct;
    $("#res-foot").textContent = st.responses + " RESPONSES · THANK YOU";

    lastMode = targetMode;
  }

  // render "IS CONVENIENCE MAKING US WEAKER?" with the last word in red
  function setQuestionTitle(sel, topic, redLast) {
    const el = $(sel);
    if (!redLast) { el.textContent = topic.toUpperCase(); return; }
    const words = topic.toUpperCase().split(" ");
    const last = words.pop();
    el.innerHTML = words.join(" ") + " <b>" + last + "</b>";
  }

  function renderPoll(st) {
    const list = $("#poll-list");
    // a second round is a fresh vote — drop the previous selection
    if (list.dataset.round !== String(st.poll.round)) {
      list.dataset.round = String(st.poll.round);
      myPoll = null;
    }
    const ids = st.poll.options.map((o) => o.id).join(",");
    if (list.dataset.ids !== ids) {
      list.innerHTML = "";
      list.dataset.ids = ids;
      st.poll.options.forEach((o) => {
        const el = document.createElement("button");
        el.className = "poll-opt"; el.dataset.id = o.id;
        el.innerHTML = '<div class="fill"></div><div class="lab">' + o.label + '</div><div class="pct">0%</div>';
        el.addEventListener("click", () => {
          myPoll = o.id; Live.send("poll", { option: o.id }); markPoll(); UI.toast("Vote counted");
        });
        list.appendChild(el);
      });
    }
    // an unrevealed poll arrives with its numbers stripped — vote, then wait
    const hidden = st.poll.options.some((o) => o.pct === null);
    list.classList.toggle("locked", hidden);
    st.poll.options.forEach((o) => {
      const el = list.querySelector('.poll-opt[data-id="' + o.id + '"]');
      el.querySelector(".pct").textContent = hidden ? "" : o.pct + "%";
      el.querySelector(".fill").style.width = hidden ? "0%" : o.pct + "%";
    });
    markPoll();
  }
  function markPoll() {
    $$("#poll-list .poll-opt").forEach((el) => el.classList.toggle("sel", el.dataset.id === myPoll));
  }

  function renderCloud(st) {
    const cloud = $("#wc-cloud");
    const words = st.wordcloud.words.slice().sort((a, b) => b.weight - a.weight).slice(0, 16);
    const max = Math.max.apply(null, words.map((w) => w.weight));
    const min = Math.min.apply(null, words.map((w) => w.weight));
    const palette = ["var(--red)", "var(--ink)", "var(--muted)", "var(--dim)"];
    cloud.innerHTML = "";
    words.forEach((w, i) => {
      const t = max === min ? 1 : (w.weight - min) / (max - min);
      const size = 15 + t * 34; // px
      const span = document.createElement("span");
      span.textContent = w.text;
      span.style.fontSize = size.toFixed(0) + "px";
      span.style.color = t > 0.75 ? "var(--red)" : (t > 0.45 ? "var(--ink)" : (i % 2 ? "var(--muted)" : "var(--dim)"));
      cloud.appendChild(span);
    });
  }

  // if this tab already joined a room, reconnect to it after a reload
  const savedRoom = sessionStorage.getItem("upgrade_room");
  if (joined && savedRoom) Live.setRoom(savedRoom);

  // back to the join screen to retype a code
  $("#nosuch-retry").addEventListener("click", () => {
    sessionStorage.removeItem("upgrade_joined");
    sessionStorage.removeItem("upgrade_room");
    location.href = "/";
  });

  // ---- closing screen: the debrief sign-up ----
  const LEAD_KEY = "upgrade_lead_done";
  if (sessionStorage.getItem(LEAD_KEY) === "1") showLeadDone();

  function showLeadDone() {
    $("#lead-form").hidden = true;
    $("#lead-done").hidden = false;
  }

  $("#lead-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = $("#lead-email").value.trim();
    const note = $("#lead-note");
    note.classList.remove("err");
    if (!email) { $("#lead-email").focus(); return; }

    const btn = $("#lead-submit");
    btn.disabled = true;
    btn.textContent = "SENDING…";
    try {
      const r = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, name: $("#lead-name").value.trim(), room: Live.room() }),
      });
      const res = await r.json();
      if (!res.ok) {
        note.textContent = res.error || "Couldn't save that — try again?";
        note.classList.add("err");
        btn.disabled = false;
        btn.textContent = "SEND ME THE DEBRIEF →";
        return;
      }
      sessionStorage.setItem(LEAD_KEY, "1");
      showLeadDone();
    } catch (err) {
      note.textContent = "No connection — try again in a moment.";
      note.classList.add("err");
      btn.disabled = false;
      btn.textContent = "SEND ME THE DEBRIEF →";
    }
  });

  Live.onStatus((up) => { $("#net-chip").hidden = up; });

  Live.onState(render);
  Live.connect("audience");
  render(Live.get());
})();
