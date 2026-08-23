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

  // Who is in the room, on the wall — the moment that turns a crowd into a room.
  function paintRoomStats(st) {
    const rs = st.roomStats;
    const on = st.screen === "stats" && rs && rs.checkedIn > 0 && !st.closed;
    $("#stats-veil").hidden = !on;
    if (!on) return;
    $("#sv-n").textContent = rs.checkedIn;

    const top = Math.max(1, ...rs.occupations.map((o) => o.count));
    const host = $("#sv-occ");
    host.innerHTML = "";
    // a projector has room for the shape of the room, not a long tail
    rs.occupations.slice(0, 7).forEach((o) => {
      const row = document.createElement("div");
      row.className = "sv-bar";
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

    const vhost = $("#sv-vibes");
    vhost.innerHTML = "";
    rs.vibes.filter((v) => v.count > 0)
      .sort((a, b) => b.count - a.count)
      .forEach((v) => {
        const cell = document.createElement("div");
        cell.className = "sv-vibe";
        const e = document.createElement("span");
        e.className = "e";
        e.textContent = v.char;
        const n = document.createElement("span");
        n.className = "n";
        n.textContent = v.count;
        const l = document.createElement("span");
        l.className = "l";
        l.textContent = v.label.toUpperCase();
        cell.append(e, n, l);
        vhost.appendChild(cell);
      });
  }

  // ---- how it works ----
  // A room that has never used this needs telling once, and a person walking in
  // reads a wall for about six seconds. So: short slides, big type, no voice-over
  // and nothing to press. It runs itself and loops until taken down.
  const HOW_DWELL = 20000;        // long enough to read, watch, and understand

  const mk = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  // Each demo is a small working mock of the feature, animated on a loop in CSS
  // and built from the app's own vocabulary — so when the real thing appears a
  // minute later, the room has already seen it.
  const DEMOS = {
    join(st) {
      const phone = mk("div", "d-phone");
      const screen = mk("div", "d-screen");
      const qr = mk("div", "d-qr");
      const img = document.createElement("img");
      img.src = "/qr.svg?url=" + encodeURIComponent(st.joinUrl || "");
      img.alt = "";
      qr.appendChild(img);
      screen.append(qr, mk("div", "d-sub", (st.code || "")));
      const done = mk("div", "d-in");
      done.append(mk("div", "d-tick", "\u2713"), mk("div", "d-lab", "YOU'RE IN"));
      phone.append(screen, mk("div", "d-scan"), done);
      return phone;
    },
    stand() {
      const phone = mk("div", "d-phone");
      const screen = mk("div", "d-screen");
      const box = mk("div", "d-choices");
      box.append(mk("div", "d-choice a", "AGREE"),
                 mk("div", "d-choice b", "DISAGREE"),
                 mk("div", "d-choice c", "UNSURE"));
      screen.appendChild(box);
      phone.appendChild(screen);
      return phone;
    },
    vote() {
      const bars = mk("div", "d-bars");
      for (let i = 0; i < 4; i++) {
        const b = mk("div", "d-bar");
        b.appendChild(document.createElement("i"));
        bars.appendChild(b);
      }
      return bars;
    },
    ask() {
      const cards = mk("div", "d-cards");
      [12, 8, 3].forEach((n) => {
        const c = mk("div", "d-card");
        c.append(mk("div", "d-vote", "\u25B2 " + n), mk("div", "d-qtext"));
        cards.appendChild(c);
      });
      return cards;
    },
    mic() {
      const row = mk("div", "d-faces");
      ["AR", "ML", "JT"].forEach((i) => row.appendChild(mk("div", "d-face", i)));
      return row;
    },
    react() {
      const box = mk("div", "d-react");
      ["\u{1F525}", "\u{1F44F}", "\u2764\uFE0F", "\u{1F914}", "\u{1F389}"]
        .forEach((e) => box.appendChild(mk("span", "d-em", e)));
      return box;
    },
    mail() {
      const m = mk("div", "d-mail");
      const bd = mk("div", "bd");
      bd.append(mk("div", "ln"), mk("div", "ln s"), mk("div", "ln"),
                mk("div", "btn"));
      m.append(mk("div", "hd"), bd);
      return m;
    },
  };

  const HOW_SLIDES = [
    { demo: "join", head: ["SCAN TO", "JOIN"],
      body: "Point your camera at the code. It opens in your browser — no app, no sign-up, no account to make." },
    { demo: "stand", head: ["SAY WHERE", "YOU STAND"],
      body: "Agree, disagree or unsure on every question. Change your mind whenever you like — the wall moves with you, and changing it is the interesting part." },
    { demo: "vote", head: ["VOTE ON", "EVERYTHING"],
      body: "Polls, word clouds, sliders and rankings. Everyone answers at once and the results build on the screen in front of you." },
    { demo: "ask", head: ["ASK THE", "QUESTION"],
      body: "Put a question to the room and upvote the ones you want answered. The best-backed questions go up on the big screen." },
    { demo: "mic", head: ["TAKE THE", "MIC"],
      body: "Disagree out loud. Join the challenge queue and the host can bring you in to say it to the room." },
    { demo: "react", head: ["REACT AS", "IT HAPPENS"],
      body: "Hold an emoji and it floats up the wall. React to what's being said without interrupting anyone." },
    { demo: "mail", head: ["TAKE IT", "HOME"],
      body: "Leave your email at the end and the full results — every topic, every vote, every question — land in your inbox." },
  ];

  let howTimer = null;
  let howAt = 0;

  function howSlide(st) {
    const stage = $("#how-stage");
    const slide = HOW_SLIDES[howAt % HOW_SLIDES.length];
    stage.innerHTML = "";
    const txt = document.createElement("div");
    const n = document.createElement("div");
    n.className = "how-n";
    n.textContent = String(howAt % HOW_SLIDES.length + 1).padStart(2, "0")
      + " / " + String(HOW_SLIDES.length).padStart(2, "0");
    const h = document.createElement("h1");
    h.className = "how-h";
    h.append(document.createTextNode(slide.head[0]));
    const b = document.createElement("b");
    b.textContent = slide.head[1];
    h.appendChild(b);
    const p = document.createElement("p");
    p.className = "how-p";
    p.textContent = slide.body;
    txt.append(n, h, p);

    const art = document.createElement("div");
    art.className = "how-art";
    art.appendChild((DEMOS[slide.demo] || DEMOS.join)(st));
    stage.append(txt, art);

    const rail = $("#how-rail");
    rail.innerHTML = "";
    HOW_SLIDES.forEach((_, i) => {
      const t = document.createElement("div");
      const at = howAt % HOW_SLIDES.length;
      t.className = "how-tick" + (i < at ? " done" : i === at ? " now" : "");
      t.style.setProperty("--dwell", HOW_DWELL + "ms");
      t.appendChild(document.createElement("span")).className = "fill";
      rail.appendChild(t);
    });

    $("#how-brand").textContent = (st.brand || "") + " LIVE";
    $("#how-join").textContent = (st.joinUrl || "").replace(/^https?:\/\//, "");
  }

  function paintHow(st) {
    const on = st.screen === "explainer" && !st.closed;
    $("#how-veil").hidden = !on;
    if (!on) {
      clearInterval(howTimer);
      howTimer = null;
      return;
    }
    if (howTimer) return;          // already running; don't restart on every snapshot
    howAt = 0;
    howSlide(st);
    howTimer = setInterval(() => {
      howAt += 1;
      howSlide(Live.get() || st);
    }, HOW_DWELL);
  }

  // The small join code that stays put once the night is running. Hidden while
  // a takeover is up (it would sit under it anyway) and while the big join
  // screen or the closing screen is showing their own, larger one.
  let cornerFor = null;
  function paintJoinCorner(st) {
    // The holding loop is the longest anyone looks at one screen — before the
    // doors, in the break — so the way in stays on it. Every other takeover
    // hides it: they are asking for attention, not offering a door.
    const bigOneShowing = !st.started || st.closed || st.exists === false;
    const on = !bigOneShowing && (!st.screen || st.screen === "holding");
    $("#join-corner").hidden = !on;
    if (!on) return;
    if (cornerFor !== st.joinUrl) {
      cornerFor = st.joinUrl;
      $("#corner-qr").src = "/qr.svg?url=" + encodeURIComponent(st.joinUrl || "");
      $("#corner-url").textContent = (st.joinUrl || "").replace(/^https?:\/\//, "");
    }
  }

  // The room filling up, under the join code, while people arrive.
  function paintLobby(st) {
    const rs = st.roomStats;
    const any = rs && rs.checkedIn > 0;
    $("#wait-room").hidden = !any;
    if (!any) return;
    $("#wr-n").textContent = rs.checkedIn;

    const top = Math.max(1, ...rs.occupations.map((o) => o.count));
    const host = $("#wr-occ");
    host.innerHTML = "";
    rs.occupations.slice(0, 5).forEach((o) => {
      const row = document.createElement("div");
      row.className = "wr-bar";
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

    const vhost = $("#wr-vibes");
    vhost.innerHTML = "";
    rs.vibes.filter((v) => v.count > 0).sort((a, b) => b.count - a.count)
      .slice(0, 6).forEach((v) => {
        const cell = document.createElement("div");
        cell.className = "wr-vibe";
        const e = document.createElement("span");
        e.className = "e";
        e.textContent = v.char;
        const n = document.createElement("span");
        n.className = "n";
        n.textContent = v.count;
        cell.append(e, n);
        vhost.appendChild(cell);
      });
  }

  // The holding loop. Muted is not a style choice: a browser will refuse to
  // autoplay anything with sound, and nobody is at the projector to press play.
  function paintHolding(st) {
    const on = st.screen === "holding";
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
    const o = st.promo;
    const live = Boolean(o && !st.closed);
    $("#offer-veil").hidden = !live;
    if (!live) return;
    const hero = $("#ov-hero");
    if (o.image) { hero.src = "/offers/" + o.image; hero.hidden = false; }
    else { hero.hidden = true; }
    $("#offer-veil").classList.toggle("no-hero", !o.image);
    $("#ov-tag").textContent = o.eyebrow || "TONIGHT ONLY";
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
  // A challenge landing is a moment worth seeing: the room pushing back, live.
  // Only ever the person, though — 180 characters nobody has read do not belong
  // on a wall in front of a hundred people.
  let knownChallenges = null;
  let pulseTimer = null;

  function paintChallengePulse(st) {
    const list = st.challenges || [];
    const ids = new Set(list.map((c) => c.id));
    if (knownChallenges === null) {     // first snapshot: catch up, don't flash
      knownChallenges = ids;
      return;
    }
    const fresh = list.find((c) => !knownChallenges.has(c.id));
    knownChallenges = ids;
    if (!fresh || st.closed) return;

    // initials, like the queue in the control room — a challenge record has no
    // photo on it, and inventing one here would go stale the moment they change it
    $("#cp-face").textContent = fresh.initials || "?";
    $("#cp-name").textContent = fresh.name || "Someone";
    const pulse = $("#ch-pulse");
    pulse.hidden = false;
    clearTimeout(pulseTimer);
    pulseTimer = setTimeout(() => { pulse.hidden = true; }, 4200);
  }

  function paintFeaturedChallenge(st) {
    const c = (st.challenges || []).find((x) => x.id === st.featuredChallenge);
    $("#ch-veil").hidden = !c;
    if (!c) return;
    $("#cv-text").textContent = c.text;
    $("#cv-name").textContent = c.name;
  }

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
    paintRoomStats(st);
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
    paintFeaturedChallenge(st);
    paintChallengePulse(st);
    paintFeaturedProfile(st);
    paintOffer(st);
    $("#closed-veil").hidden = !st.closed;
    // Before the crew starts, the wall stays on the join code and the room
    // filling up behind it — never on the first debate question, which nobody
    // should read before it is asked.
    $("#waiting-veil").hidden = st.closed || (st.started && st.inRoom > 0);
    paintLobby(st);
    paintJoinCorner(st);
    paintHow(st);
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
