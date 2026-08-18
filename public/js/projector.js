// Projector Room Display — swaps big-screen views to follow the moderator's mode.
(function () {
  const $ = (s) => document.querySelector(s);

  const MODE_VIEW = {
    discussion: "pv-discussion", results: "pv-discussion",
    poll: "pv-poll", wordcloud: "pv-wordcloud", emoji: "pv-emoji",
    slider: "pv-slider", ranking: "pv-ranking",
  };

  function redLast(topic) {
    const w = topic.split(" ");
    const last = w.pop();
    return w.join(" ") + " <b>" + last + "</b>";
  }

  function ring(st) {
    const wn = st.whatsNext;
    const pct = wn.threshold ? Math.min(100, (wn.votes / wn.threshold) * 100) : 0;
    $("#p-ring").innerHTML =
      '<circle cx="18" cy="18" r="15.9" fill="none" stroke="#1c1c20" stroke-width="2.4"/>' +
      '<circle cx="18" cy="18" r="15.9" fill="none" stroke="#FF2D46" stroke-width="2.8" stroke-linecap="round"' +
      ' stroke-dasharray="' + pct + ' ' + (100 - pct) + '" stroke-dashoffset="25" pathlength="100"/>';
  }

  function showView(id) {
    document.querySelectorAll(".pview").forEach((v) => v.classList.toggle("active", v.id === id));
  }

  // The holding loop. Muted is not a style choice: a browser will refuse to
  // autoplay anything with sound, and nobody is at the projector to press play.
  function paintHolding(st) {
    const on = Boolean(st.holdingLive);
    const veil = $("#hold-veil");
    const video = $("#hold-video");
    if (on === !veil.hidden) return;          // already in the right state
    veil.hidden = !on;
    if (on) {
      const go = video.play();
      if (go && go.catch) go.catch(() => {}); // a blocked play leaves frame one up
    } else {
      video.pause();
    }
  }

  // The offer takes the screen only while the moderator holds it up.
  let offerQrFor = null;
  function paintOffer(st) {
    const o = st.offer;
    const live = Boolean(o && st.offerLive && !st.closed);
    $("#offer-veil").hidden = !live;
    if (!live) return;
    const hero = $("#ov-hero");
    if (o.image) { hero.src = "/offers/" + o.image; hero.hidden = false; }
    else { hero.hidden = true; }
    $("#offer-veil").classList.toggle("no-hero", !o.image);
    $("#ov-head").textContent = o.headline;
    $("#ov-body").textContent = o.body || "";
    $("#ov-body").hidden = !o.body;
    $("#ov-cta").innerHTML = "Tap <b>" + (o.cta || "I'M INTERESTED") + "</b> on your phone";
    // a QR for anyone not already in the app
    $("#ov-qr").hidden = !o.link;
    if (o.link && offerQrFor !== o.link) {
      offerQrFor = o.link;
      $("#ov-qr-img").src = "/qr.svg?url=" + encodeURIComponent(o.link);
      $("#ov-link").textContent = o.link.replace(/^https?:\/\//, "");
    }
  }

  // Someone the moderator has brought in. Profiles reach this screen only when
  // the crew deliberately features them.
  function paintFeaturedProfile(st) {
    const p = st.featuredProfile;
    $("#who-veil").hidden = !p;
    if (!p) return;
    const face = $("#wv-face");
    face.style.backgroundImage = p.avatar ? "url(/avatars/" + p.avatar + ")" : "";
    face.classList.toggle("has", Boolean(p.avatar));
    $("#wv-initials").textContent = p.avatar ? "" : (p.initials || "?");
    $("#wv-name").textContent = p.name || "From the floor";
    $("#wv-occ").textContent = p.occupation || "";
    $("#wv-occ").hidden = !p.occupation;
    $("#wv-fact").textContent = p.fact ? "“" + p.fact + "”" : "";
    $("#wv-fact").hidden = !p.fact;
  }

  // A question the moderator has put up sits over whatever else is showing.
  function paintFeaturedQuestion(st) {
    const q = (st.questions || []).find((x) => x.id === st.featuredQuestion);
    $("#q-veil").hidden = !q;
    if (!q) return;
    $("#qv-text").textContent = q.text;
    $("#qv-name").textContent = q.name;
    $("#qv-votes").textContent = q.votes;
  }

  // The QR is rendered by the server; only reload it when the room changes.
  let qrCode = null;
  function paintJoin(st) {
    const pretty = (st.joinUrl || "").replace(/^https?:\/\//, "");
    $("#wait-wordmark").innerHTML = st.brand + " <em>LIVE</em>";
    $("#wait-event").textContent = st.eventName;
    $("#wait-url").textContent = pretty;
    $("#closed-url").textContent = pretty;
    $("#wait-code").textContent = st.code;
    $("#closed-code").textContent = st.code;
    if (qrCode !== st.code) {
      qrCode = st.code;
      const src = "/qr.svg?room=" + encodeURIComponent(st.code);
      $("#wait-qr").src = src;
      $("#closed-qr").src = src;
    }
  }

  // Count a number up to its target — the reveal should land, not just appear.
  function countUp(el, target, suffix) {
    const start = performance.now();
    const dur = 900;
    if (el._raf) cancelAnimationFrame(el._raf);
    const step = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(target * eased) + suffix;
      if (t < 1) el._raf = requestAnimationFrame(step);
    };
    el._raf = requestAnimationFrame(step);
  }

  // true only on the frame the moderator reveals, so the animation fires once
  let wasRevealed = true;
  let justRevealed = false;

  function render(st) {
    if (!st) return;
    justRevealed = st.revealed && !wasRevealed;
    wasRevealed = st.revealed;
    if (justRevealed) document.body.classList.add("reveal-flash");
    if (justRevealed) setTimeout(() => document.body.classList.remove("reveal-flash"), 900);
    $("#wordmark").innerHTML = st.brand + " <em>LIVE</em>";
    paintHolding(st);
    // a code nobody opened — nothing to display until setup opens it
    const missing = st.exists === false;
    $("#nosuch-veil").hidden = !missing;
    $("#nosuch-code").textContent = st.code;
    if (missing) {
      $("#closed-veil").hidden = true;
      $("#waiting-veil").hidden = true;
      return;
    }
    paintJoin(st);
    paintFeaturedQuestion(st);
    paintFeaturedProfile(st);
    paintOffer(st);
    $("#closed-veil").hidden = !st.closed;
    // nobody in the room yet — show the join code rather than an empty stage
    $("#waiting-veil").hidden = st.closed || st.inRoom > 0;
    $("#p-n").textContent = st.topicIndex + 1;
    $("#p-t").textContent = st.topicCount;
    showView(MODE_VIEW[st.mode] || "pv-discussion");

    let foot = "";

    // discussion / results
    $("#p-q").innerHTML = redLast(st.topic);
    $("#p-agree").textContent = st.sentiment.agreePct + "%";
    $("#p-disagree").textContent = st.sentiment.disagreePct + "%";
    $("#p-unsure").textContent = st.sentiment.unsurePct + "%";
    $("#p-wn-num").textContent = st.whatsNext.votes;
    $("#p-wn-den").textContent = st.whatsNext.threshold;
    ring(st);
    $("#p-ch-count").textContent = st.challenges.length;
    const show = st.challenges.slice(0, 6);
    const extra = st.challenges.length - show.length;
    $("#p-avs").innerHTML = show.map((c) => '<div class="av">' + c.initials + '</div>').join("") +
      (extra > 0 ? '<div class="av more">+' + extra + '</div>' : "");

    // poll — bars stay flat and blank until the moderator reveals
    $("#pp-q").innerHTML = redLast(st.poll.question);
    $("#pp-round").textContent = st.poll.round > 1 ? "ROUND " + st.poll.round + " · AFTER THE DEBATE" : "";
    $("#pp-round").hidden = st.poll.round <= 1;
    const pollHidden = st.poll.options.some((o) => o.pct === null);
    const lead = pollHidden ? -1 : Math.max.apply(null, st.poll.options.map((o) => o.pct));
    const pollKey = st.poll.question + "|" + st.poll.options.map((o) => o.id).join(",");
    const before = st.poll.before || null;
    const barKey = pollKey + "|r" + st.poll.round + "|" + (before ? "b" : "");
    if ($("#pp-bars").dataset.key !== barKey) {
      $("#pp-bars").dataset.key = barKey;
      $("#pp-bars").innerHTML = st.poll.options.map(() =>
        '<div class="pv-bar"><div class="ghost"></div><div class="fill"></div>' +
        '<div class="lab"></div><div class="nums"><span class="shift"></span>' +
        '<span class="pct"></span></div></div>').join("");
      Array.from($("#pp-bars").children).forEach((bar, i) => {
        bar.querySelector(".lab").textContent = st.poll.options[i].label;
      });
    }
    Array.from($("#pp-bars").children).forEach((bar, i) => {
      const o = st.poll.options[i];
      if (!o) return;
      bar.classList.toggle("lead", !pollHidden && o.pct === lead && lead > 0);
      bar.querySelector(".fill").style.width = pollHidden ? "0%" : o.pct + "%";

      // where the room stood before the discussion, as a ghost mark on the bar
      const wasPct = before ? before[i].pct : null;
      const ghost = bar.querySelector(".ghost");
      ghost.style.width = wasPct === null ? "0%" : wasPct + "%";
      ghost.hidden = wasPct === null;

      const pct = bar.querySelector(".pct");
      const shift = bar.querySelector(".shift");
      if (pollHidden) { pct.textContent = ""; shift.textContent = ""; return; }
      if (justRevealed) countUp(pct, o.pct, "%");
      else pct.textContent = o.pct + "%";

      if (wasPct === null) { shift.textContent = ""; shift.className = "shift"; return; }
      const d = o.pct - wasPct;
      shift.textContent = d === 0 ? "±0" : (d > 0 ? "▲ +" + d : "▼ " + d);
      shift.className = "shift " + (d > 0 ? "up" : d < 0 ? "down" : "flat");
    });
    $("#pp-bars").classList.toggle("locked", pollHidden);
    $("#pp-locked").hidden = !pollHidden;
    $("#pp-locked-n").textContent = st.responses;

    // word cloud
    $("#pw-q").innerHTML = redLast(st.wordcloud.question);
    renderCloud(st);

    // emoji
    $("#pe-q").innerHTML = redLast(st.emoji.question);
    $("#pe-grid").innerHTML = st.emoji.reactions.map((r) =>
      '<div class="cell"><div class="e">' + r.char + '</div><div class="c">' + r.count + '</div></div>').join("");

    // slider
    $("#ps-q").innerHTML = redLast(st.slider.question);
    // the room average is withheld until the reveal
    const avg = st.slider.avg;
    const sliderHidden = avg === null;
    if (sliderHidden) {
      $("#ps-pct").textContent = "—";
      $("#ps-lab").textContent = st.responses + " ANSWERS IN · HIDDEN";
      $("#ps-fill").style.width = "0%";
      $("#ps-knob").style.left = "0%";
    } else {
      if (justRevealed) countUp($("#ps-pct"), avg, "");
      else $("#ps-pct").textContent = avg;
      $("#ps-lab").textContent = sliderLabel(st, avg).toUpperCase();
      $("#ps-fill").style.width = avg + "%";
      $("#ps-knob").style.left = avg + "%";
    }
    $("#ps-left").textContent = "0% · " + st.slider.leftLabel.toUpperCase();
    $("#ps-right").textContent = "100% · " + st.slider.rightLabel.toUpperCase();

    // ranking
    $("#pr-q").innerHTML = redLast(st.ranking.question);
    $("#pr-list").innerHTML = st.ranking.items.map((it, i) =>
      '<div class="row"><div class="num">' + (i + 1) + '</div><div class="lab">' + it.label + '</div><div class="grip">⣿</div></div>').join("");

    // footer response count for interaction modes
    if (["poll", "wordcloud", "emoji", "slider", "ranking"].includes(st.mode)) {
      foot = st.responses + " RESPONSES · ROOM " + st.code;
    } else {
      foot = "";
    }
    $("#pv-foot").textContent = foot;
  }

  // The readout under the % comes from the topic's own labels: an explicit
  // resultLabel if the event defines one, otherwise whichever end it leans to.
  function sliderLabel(st, v) {
    if (st.slider.resultLabel) return st.slider.resultLabel;
    return v >= 50 ? st.slider.rightLabel : st.slider.leftLabel;
  }

  function renderCloud(st) {
    const words = st.wordcloud.words.slice().sort((a, b) => b.weight - a.weight).slice(0, 22);
    const cloud = $("#pw-cloud");
    if (!words.length) { cloud.innerHTML = ""; return; }
    const max = Math.max.apply(null, words.map((w) => w.weight));
    const min = Math.min.apply(null, words.map((w) => w.weight));
    cloud.innerHTML = "";
    words.forEach((w, i) => {
      const t = max === min ? 1 : (w.weight - min) / (max - min);
      const span = document.createElement("span");
      span.textContent = w.text;
      span.style.fontSize = (28 + t * 78).toFixed(0) + "px";
      span.style.color = t > 0.75 ? "var(--red)" : (t > 0.45 ? "var(--ink)" : (i % 2 ? "var(--muted)" : "var(--dim)"));
      cloud.appendChild(span);
    });
  }

  // ---- reaction bursts ----
  // Each one is a short-lived element that drifts up and fades. Capped so a
  // very enthusiastic room can't pile up thousands of nodes on the display.
  const MAX_FLOATERS = 70;
  const layer = $("#burst-layer");

  Live.onBurst((emoji) => {
    if (document.hidden) return;
    if (layer.childElementCount >= MAX_FLOATERS) return;

    const el = document.createElement("span");
    el.className = "floater";
    el.textContent = emoji;
    // spread them across the width, vary size, speed and drift so a stream of
    // the same emoji doesn't look like a single column
    el.style.left = (4 + Math.random() * 92).toFixed(2) + "%";
    el.style.fontSize = (34 + Math.random() * 34).toFixed(0) + "px";
    const dur = 3.4 + Math.random() * 2.2;
    el.style.animationDuration = dur.toFixed(2) + "s";
    el.style.setProperty("--drift", (Math.random() * 120 - 60).toFixed(0) + "px");
    layer.appendChild(el);
    setTimeout(() => el.remove(), dur * 1000 + 120);
  });

  Live.onState(render);
  Live.connect("projector");
})();
