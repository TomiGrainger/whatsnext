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

  function render(st) {
    if (!st) return;
    $("#wordmark").innerHTML = st.brand + " <em>LIVE</em>";
    paintJoin(st);
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

    // poll
    $("#pp-q").innerHTML = redLast(st.poll.question);
    const lead = Math.max.apply(null, st.poll.options.map((o) => o.pct));
    $("#pp-bars").innerHTML = st.poll.options.map((o) =>
      '<div class="pv-bar' + (o.pct === lead && lead > 0 ? " lead" : "") + '">' +
        '<div class="fill" style="width:' + o.pct + '%"></div>' +
        '<div class="lab">' + o.label + '</div><div class="pct">' + o.pct + '%</div></div>').join("");

    // word cloud
    $("#pw-q").innerHTML = redLast(st.wordcloud.question);
    renderCloud(st);

    // emoji
    $("#pe-q").innerHTML = redLast(st.emoji.question);
    $("#pe-grid").innerHTML = st.emoji.reactions.map((r) =>
      '<div class="cell"><div class="e">' + r.char + '</div><div class="c">' + r.count + '</div></div>').join("");

    // slider
    $("#ps-q").innerHTML = redLast(st.slider.question);
    const avg = st.slider.avg;
    $("#ps-pct").textContent = avg;
    $("#ps-lab").textContent = sliderLabel(st, avg).toUpperCase();
    $("#ps-fill").style.width = avg + "%";
    $("#ps-knob").style.left = avg + "%";
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

  Live.onState(render);
  Live.connect("projector");
})();
