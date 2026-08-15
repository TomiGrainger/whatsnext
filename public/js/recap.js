// The public debrief — built entirely from /api/recap for one room.
(function () {
  const $ = (s) => document.querySelector(s);
  const room = (new URLSearchParams(location.search).get("room") || "WN25")
    .replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);

  const KIND_LABEL = {
    poll: "Poll", wordcloud: "Word cloud", slider: "Slider",
    emoji: "Reactions", ranking: "Ranking",
  };

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  function stat(value, label) {
    const box = el("div", "rc-stat");
    box.append(el("div", "v", value), el("div", "k", label));
    return box;
  }

  function render(d) {
    $("#rc-brand").innerHTML = d.brand + " <em>LIVE</em>";
    $("#rc-foot-brand").innerHTML = d.brand + " <em>LIVE</em>";
    $("#rc-event").textContent = d.eventName;
    $("#rc-code").textContent = d.code;
    document.title = d.eventName + " — The Debrief";

    // headline numbers
    const answered = d.topics.reduce((n, t) => n + t.responses, 0);
    const interactions = d.topics.reduce((n, t) => n + t.interactions.length, 0);
    $("#rc-stats").append(
      stat(d.topicCount, d.topicCount === 1 ? "TOPIC" : "TOPICS"),
      stat(interactions, "INTERACTIONS"),
      stat(answered, "RESPONSES"),
      stat(d.questions.length, "QUESTIONS"));

    const host = $("#rc-topics");
    d.topics.forEach((t, i) => {
      const sec = el("section", "rc-topic");
      sec.append(el("div", "rc-num", String(i + 1).padStart(2, "0")));
      sec.append(el("h2", "rc-q", t.question));

      if (t.sentiment.any) {
        const row = el("div", "rc-sent");
        [["agree", "AGREE"], ["disagree", "DISAGREE"], ["unsure", "UNSURE"]].forEach(([k, lab]) => {
          const b = el("div", "s " + k);
          b.append(el("div", "n", t.sentiment[k + "Pct"] + "%"), el("div", "l", lab));
          row.appendChild(b);
        });
        sec.appendChild(row);
      } else {
        sec.appendChild(el("div", "rc-none", "No votes recorded on this topic."));
      }

      t.interactions.forEach((it) => sec.appendChild(interaction(it)));
      host.appendChild(sec);
    });

    if (d.questions.length) {
      $("#rc-questions-block").hidden = false;
      const qs = $("#rc-questions");
      d.questions.forEach((q) => {
        const row = el("div", "rc-qrow" + (q.answered ? " answered" : ""));
        const v = el("div", "rc-qv");
        v.append(el("span", "ar", "▲"), el("span", "n", String(q.votes)));
        const body = el("div", "rc-qt", q.text);
        row.append(v, body);
        if (q.answered) row.appendChild(el("div", "rc-qtag", "ANSWERED"));
        qs.appendChild(row);
      });
    }

    $("#rc-wrap").hidden = false;
  }

  function interaction(it) {
    const box = el("div", "rc-int");
    const head = el("div", "rc-int-head");
    head.append(el("span", "kind", KIND_LABEL[it.kind] || it.kind),
                el("span", "q", it.question));
    box.appendChild(head);

    if (it.kind === "poll") {
      if (it.before) {
        box.appendChild(el("div", "rc-shiftnote",
          "Asked again after the discussion — the arrows show how far the room moved."));
      }
      const bars = el("div", "rc-bars");
      it.options.forEach((o, i) => {
        const bar = el("div", "rc-bar");
        const fill = el("div", "fill");
        fill.style.width = o.pct + "%";
        const lab = el("div", "lab", o.label);
        const nums = el("div", "nums");
        if (it.before) {
          const d = o.pct - it.before[i].pct;
          const sh = el("span", "shift " + (d > 0 ? "up" : d < 0 ? "down" : "flat"),
            d === 0 ? "±0" : (d > 0 ? "▲ +" + d : "▼ " + d));
          nums.appendChild(sh);
        }
        nums.appendChild(el("span", "pct", o.pct + "%"));
        bar.append(fill, lab, nums);
        bars.appendChild(bar);
      });
      box.appendChild(bars);
    }

    if (it.kind === "wordcloud" && it.words.length) {
      const cloud = el("div", "rc-cloud");
      const max = Math.max.apply(null, it.words.map((w) => w.weight));
      const min = Math.min.apply(null, it.words.map((w) => w.weight));
      it.words.forEach((w, i) => {
        const t = max === min ? 1 : (w.weight - min) / (max - min);
        const s = el("span", null, w.text);
        s.style.fontSize = (15 + t * 30).toFixed(0) + "px";
        s.style.color = t > 0.72 ? "var(--red)" : (t > 0.4 ? "var(--ink)" : (i % 2 ? "var(--muted)" : "var(--dim)"));
        cloud.appendChild(s);
      });
      box.appendChild(cloud);
    }

    if (it.kind === "emoji") {
      const grid = el("div", "rc-emoji");
      it.reactions.forEach((r) => {
        const cell = el("div", "cell");
        cell.append(el("div", "e", r.char), el("div", "c", String(r.count)));
        grid.appendChild(cell);
      });
      box.appendChild(grid);
    }

    if (it.kind === "slider") {
      const s = el("div", "rc-slider");
      s.append(el("div", "big", it.avg + "%"));
      const track = el("div", "track");
      const fill = el("div", "fill");
      fill.style.width = it.avg + "%";
      track.appendChild(fill);
      s.appendChild(track);
      const ends = el("div", "ends");
      ends.append(el("span", null, "0% · " + it.leftLabel), el("span", null, "100% · " + it.rightLabel));
      s.appendChild(ends);
      box.appendChild(s);
    }

    if (it.kind === "ranking") {
      const list = el("div", "rc-rank");
      it.items.forEach((item, i) => {
        const row = el("div", "row");
        row.append(el("div", "num", String(i + 1)), el("div", "lab", item.label));
        list.appendChild(row);
      });
      box.appendChild(list);
    }

    box.appendChild(el("div", "rc-int-foot",
      it.responses + (it.responses === 1 ? " response" : " responses")));
    return box;
  }

  fetch("/api/recap?room=" + encodeURIComponent(room), { headers: { "Cache-Control": "no-store" } })
    .then((r) => r.json())
    .then((d) => { if (d.exists) render(d); else $("#rc-empty").hidden = false; })
    .catch(() => { $("#rc-empty").hidden = false; });
})();
