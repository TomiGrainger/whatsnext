// The Rooms — the crew's view of the archive.
// Everything here is read-only: this page never changes what happened, it only
// shows it. Audience text (names, questions, words) is set with textContent
// throughout, never innerHTML — it came from strangers' phones.
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const initials = (name, email) => {
    const parts = (name || "").split(" ").filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (email || "?").slice(0, 2).toUpperCase();
  };

  const when = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  };

  const ago = (ts) => {
    if (!ts) return "never";
    const days = Math.floor((Date.now() / 1000 - ts) / 86400);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return days + " days ago";
    if (days < 365) return Math.round(days / 30) + " months ago";
    return Math.round(days / 365) + "y ago";
  };

  async function get(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (r.status === 401) { location.href = "/login?next=/crm"; return null; }
    if (!r.ok) return null;
    return r.json();
  }

  // ---- headline numbers ----
  async function loadOverview() {
    const data = await get("/api/crm/overview");
    if (!data) return;
    const s = data.summary;
    const host = $("#crm-stats");
    host.innerHTML = "";
    [["events", s.sessions, false], ["contacts", s.people, true],
     ["checked in", s.attendees, false], ["responses", s.responses, false],
     ["sign-ups", s.contacts, false]].forEach(([label, value, lit]) => {
      const card = el("div", "crm-stat" + (lit ? " lit" : ""));
      card.append(el("div", "v", String(value ?? 0)), el("div", "k", label.toUpperCase()));
      host.appendChild(card);
    });
    renderEvents(data.sessions || []);
  }

  // ---- people ----
  let searchTimer = null;

  async function loadPeople() {
    const q = $("#crm-q").value.trim();
    const sort = $("#crm-sort").value;
    const data = await get("/api/crm/people?q=" + encodeURIComponent(q) +
                           "&sort=" + encodeURIComponent(sort));
    if (!data) return;
    const rows = data.people || [];
    $("#people-empty").hidden = rows.length > 0 || Boolean(q);
    $("#people-count").textContent = q
      ? rows.length + " of " + data.total + " matching “" + q + "”"
      : data.total + (data.total === 1 ? " person" : " people");

    const table = el("table");
    const head = el("tr");
    ["", "What they do", "Events", "Hands", "Last seen"].forEach((h) => {
      head.appendChild(el("th", null, h));
    });
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    rows.forEach((p) => {
      const tr = el("tr", "row");
      tr.addEventListener("click", () => openPerson(p.id));

      const who = el("div", "who");
      who.appendChild(el("div", "face", initials(p.name, p.email)));
      const txt = el("div");
      txt.append(el("div", "nm", p.name || "—"), el("div", "em", p.email));
      who.appendChild(txt);
      const c1 = el("td");
      c1.appendChild(who);
      if (p.suppressed) c1.appendChild(el("span", "tag off", "UNSUBSCRIBED"));

      const c2 = el("td", null, p.occupation || "—");
      const c3 = el("td", "num", String(p.nights));
      const c4 = el("td", "num");
      if (p.hands) c4.appendChild(el("b", null, String(p.hands)));
      else c4.textContent = "—";
      const c5 = el("td", "num", ago(p.last_seen));
      tr.append(c1, c2, c3, c4, c5);
      body.appendChild(tr);
    });
    table.appendChild(body);
    const host = $("#people-table");
    host.innerHTML = "";
    if (rows.length) host.appendChild(table);
  }

  // ---- events ----
  function renderEvents(sessions) {
    $("#events-empty").hidden = sessions.length > 0;
    const table = el("table");
    const head = el("tr");
    ["Event", "Room", "When", "Checked in", "Sign-ups", "Responses"].forEach((h) => {
      head.appendChild(el("th", null, h));
    });
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    sessions.forEach((s) => {
      const tr = el("tr", "row");
      tr.addEventListener("click", () => openSession(s.id));
      const c1 = el("td");
      c1.appendChild(el("span", null, s.event_name || "—"));
      if (s.discarded) c1.appendChild(el("span", "tag off", " REHEARSAL"));
      else if (!s.closed_at) c1.appendChild(el("span", "tag live", " RUNNING"));
      tr.append(c1,
                el("td", null, s.room_code),
                el("td", "num", when(s.opened_at)),
                el("td", "num", String(s.attendees)),
                el("td", "num", String(s.contacts)),
                el("td", "num", String(s.responses)));
      body.appendChild(tr);
    });
    table.appendChild(body);
    const host = $("#events-table");
    host.innerHTML = "";
    if (sessions.length) host.appendChild(table);
  }

  // ---- the slide-over ----
  function openPanel() {
    $("#panel").hidden = false;
    $("#scrim").hidden = false;
  }
  function closePanel() {
    $("#panel").hidden = true;
    $("#scrim").hidden = true;
  }
  $("#panel-close").addEventListener("click", closePanel);
  $("#scrim").addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

  const KIND_LABEL = {
    sentiment: "SAID", poll: "VOTED", word: "WORD", emoji: "REACTED",
    slider: "SLIDER", ranking: "RANKED", question: "ASKED",
    challenge: "CHALLENGED", whatsnext: "MOVE ON", interested: "INTERESTED",
  };

  async function openPerson(id) {
    const p = await get("/api/crm/person?id=" + encodeURIComponent(id));
    if (!p) return;
    const body = $("#panel-body");
    body.innerHTML = "";
    body.append(el("h2", "p-name", p.name || "Unnamed"), el("div", "p-em", p.email));
    if (p.occupation) body.appendChild(el("p", "p-meta", p.occupation));

    const chips = el("div", "p-chips");
    const chip = (label, value) => {
      const c = el("span", "p-chip");
      c.append(el("b", null, String(value)), document.createTextNode(" " + label));
      return c;
    };
    chips.append(chip(p.nights === 1 ? "event" : "events", p.nights),
                 chip("hands raised", p.hands));
    if (p.suppressed) chips.appendChild(el("span", "p-chip", "Unsubscribed"));
    body.appendChild(chips);
    body.appendChild(el("p", "p-meta",
      "First seen " + when(p.first_seen) + " · last seen " + ago(p.last_seen)));

    body.appendChild(el("div", "p-h2", "WHERE THEY SIGNED UP"));
    if (!p.signups.length) {
      body.appendChild(el("p", "p-none", "No sign-ups recorded."));
    }
    p.signups.forEach((g) => {
      const card = el("div", "p-night");
      const hd = el("div", "hd");
      hd.append(el("div", "ev", (g.event_name || "Event") + " · " + (g.room_code || "—")),
                el("div", "dt", when(g.at)));
      card.appendChild(hd);
      card.appendChild(el("div", "dt", g.kind === "offer"
        ? "Raised a hand for the offer"
        : "Asked for the debrief" + (g.sent_at ? " · sent " + when(g.sent_at) : " · not sent yet")));
      body.appendChild(card);
    });

    // Said plainly, because it is the design and not a gap: there is no join
    // from a contact to an answer, so this panel cannot show one.
    const note = el("div", "p-private");
    note.append(el("b", null, "What they said isn't here, and can't be. "),
      document.createTextNode("Answers are kept as anonymous room data — "
        + "grouped per person within one event so the room can be broken down "
        + "by occupation, with nothing tying any of it back to a name."));
    body.appendChild(note);

    openPanel();
    $("#panel").scrollTop = 0;
  }

  // one stacked bar: each answer a segment, widths in per cent
  const SEG = ["seg-a", "seg-b", "seg-c", "seg-d", "seg-e"];
  function bar(answers, pcts, counts) {
    const wrap = el("div", "x-bar");
    answers.forEach((a, i) => {
      if (!pcts[i]) return;
      const seg = el("div", "x-seg " + SEG[i % SEG.length]);
      seg.style.width = pcts[i] + "%";
      seg.title = a + " — " + pcts[i] + "%" + (counts ? " (" + counts[i] + ")" : "");
      seg.textContent = pcts[i] >= 12 ? pcts[i] + "%" : "";
      wrap.appendChild(seg);
    });
    const key = el("div", "x-key");
    answers.forEach((a, i) => {
      if (!pcts[i]) return;
      const k = el("span", "x-k");
      k.appendChild(el("i", "dot " + SEG[i % SEG.length]));
      k.appendChild(document.createTextNode(a));
      key.appendChild(k);
    });
    const box = el("div");
    box.append(wrap, key);
    return box;
  }

  async function openSession(id) {
    const s = await get("/api/crm/session?id=" + encodeURIComponent(id));
    if (!s) return;
    const body = $("#panel-body");
    body.innerHTML = "";
    body.append(el("h2", "p-name", s.event_name || "Event"),
                el("div", "p-em", s.room_code + " · " + when(s.opened_at)));
    if (s.discarded) {
      body.appendChild(el("p", "p-meta",
        "This one was reset — kept as a rehearsal and left out of the totals."));
    }

    const chips = el("div", "p-chips");
    const chip = (label, value) => {
      const c = el("span", "p-chip");
      c.append(el("b", null, String(value)), document.createTextNode(" " + label));
      return c;
    };
    chips.append(chip("checked in", s.attendees), chip("signed up", s.contacts));
    body.appendChild(chips);

    if (s.occupations.length) {
      body.appendChild(el("div", "p-h2", "WHO WAS IN THE ROOM"));
      const top = Math.max(...s.occupations.map((o) => o.count), 1);
      const bars = el("div", "p-bars");
      s.occupations.forEach((o) => {
        const row = el("div", "p-bar");
        const fill = el("div", "fill");
        fill.style.width = Math.round((o.count / top) * 100) + "%";
        row.append(fill, el("span", "t", o.label), el("span", "n", String(o.count)));
        bars.appendChild(row);
      });
      body.appendChild(bars);
    }

    if (s.topics.length) {
      body.appendChild(el("div", "p-h2", "HOW THE ROOM SPLIT"));
      s.topics.forEach((t) => {
        const card = el("div", "p-night");
        card.appendChild(el("div", "ev", t.topic_question || "Topic"));
        const split = el("div", "p-split");
        [["agree", "AGREE"], ["disagree", "DISAGREE"], ["unsure", "UNSURE"]].forEach(([k, l]) => {
          const cell = el("div", k);
          cell.append(el("div", "n", String(t[k] || 0)), el("div", "l", l));
          split.appendChild(cell);
        });
        card.appendChild(split);
        body.appendChild(card);
      });
    }

    if (s.questions.length) {
      body.appendChild(el("div", "p-h2", "WHAT THEY ASKED"));
      s.questions.forEach((q) => {
        const card = el("div", "p-night");
        card.appendChild(el("div", "ev", q.text));
        body.appendChild(card);
      });
    }

    if ((s.answers || []).length) {
      body.appendChild(el("div", "p-h2", "WHAT THE ROOM THOUGHT"));
      s.answers.forEach((b) => {
        const card = el("div", "p-night");
        card.appendChild(el("div", "ev", b.question));
        card.appendChild(el("div", "dt", b.total + " answered"));

        // the whole room first, as the headline
        const overall = el("div", "x-row x-all");
        overall.appendChild(el("div", "lb", "EVERYONE"));
        overall.appendChild(bar(b.answers, b.pcts, b.counts));
        card.appendChild(overall);

        // then the same split, occupation by occupation
        b.groups.forEach((g) => {
          const row = el("div", "x-row" + (g.small ? " x-small" : ""));
          const lb = el("div", "lb", g.label);
          lb.appendChild(el("span", "n", " " + g.total));
          row.appendChild(lb);
          row.appendChild(bar(b.answers, g.pcts));
          card.appendChild(row);
        });
        body.appendChild(card);
      });
    }

    const dl = el("a", "p-dl", "DOWNLOAD THIS EVENT'S RESPONSES");
    dl.href = "/api/crm/responses.csv?id=" + encodeURIComponent(id);
    body.appendChild(dl);
    openPanel();
    $("#panel").scrollTop = 0;
  }

  // ---- tabs ----
  $$(".crm-tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".crm-tab").forEach((t) => t.classList.toggle("on", t === tab));
    $$(".crm-view").forEach((v) => v.classList.toggle("on", v.id === "view-" + tab.dataset.view));
  }));

  $("#crm-q").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadPeople, 180);
  });
  $("#crm-sort").addEventListener("change", loadPeople);

  loadOverview();
  loadPeople();
})();
