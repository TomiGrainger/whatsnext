// Event setup — build or edit an event, then launch it into a room.
(function () {
  const $ = (s) => document.querySelector(s);

  const KIND_LABEL = {
    poll: "Poll", wordcloud: "Word cloud", slider: "Slider",
    emoji: "Emoji", ranking: "Ranking",
  };
  const KINDS = ["poll", "wordcloud", "slider", "emoji", "ranking"];
  const DEFAULT_OPTIONS = { poll: ["", ""], emoji: ["🔥", "👏", "🤔", "❤️"], ranking: ["", ""] };

  let topics = [];          // the event being built or edited
  let editingId = null;     // null = creating, otherwise the event being edited
  let selectedEvent = null; // highlighted in the launch list
  let defaultEventId = null;
  let confirmingDelete = null;
  let confirmingReset = null;
  let confirmingSend = null;
  let mailConfigured = false;

  // ---------------- build: topic model ----------------
  // A topic is a discussion question plus the interactions you can launch while
  // it is on screen. The discussion itself needs no configuring beyond the
  // question — it is always there and never counts down.
  function addTopic() {
    topics.push({ question: "", threshold: 10, interactions: [] });
    renderTopics();
  }

  function addInteraction(topic, kind) {
    const it = { kind, question: "", duration: 60 };
    if (kind === "slider") { it.leftLabel = ""; it.rightLabel = ""; it.resultLabel = ""; }
    if (DEFAULT_OPTIONS[kind]) it.options = DEFAULT_OPTIONS[kind].slice();
    topic.interactions.push(it);
    renderTopics();
  }

  function renderTopics() {
    const host = $("#topics");
    host.innerHTML = "";
    $("#topic-count").textContent = topics.length;

    topics.forEach((t, i) => {
      const card = document.createElement("div");
      card.className = "topic";

      const top = document.createElement("div");
      top.className = "topic-top";
      top.innerHTML =
        '<span class="topic-n">' + String(i + 1).padStart(2, "0") + '</span>' +
        '<span class="topic-kind">Topic</span>';
      const tools = document.createElement("div");
      tools.className = "topic-tools";
      tools.append(
        tool("↑", "Move up", () => move(topics, i, -1)),
        tool("↓", "Move down", () => move(topics, i, 1)),
        tool("×", "Remove topic", () => { topics.splice(i, 1); renderTopics(); }, "del"));
      top.appendChild(tools);
      card.appendChild(top);

      card.appendChild(field(t.question, "The question to debate", (v) => (t.question = v)));
      card.appendChild(subLab("Votes to unlock “what’s next”"));
      card.appendChild(field(t.threshold, "10", (v) => (t.threshold = v), "number"));

      // ---- interactions nested under this topic ----
      card.appendChild(subLab("During this topic you can launch"));
      const list = document.createElement("div");
      list.className = "inters";
      t.interactions.forEach((it, n) => list.appendChild(interactionCard(t, it, n)));
      card.appendChild(list);

      const adds = document.createElement("div");
      adds.className = "inter-adds";
      KINDS.forEach((kind) => {
        const b = document.createElement("button");
        b.className = "mini";
        b.type = "button";
        b.textContent = "+ " + KIND_LABEL[kind].toUpperCase();
        b.addEventListener("click", () => addInteraction(t, kind));
        adds.appendChild(b);
      });
      card.appendChild(adds);

      host.appendChild(card);
    });
  }

  function interactionCard(topic, it, n) {
    const box = document.createElement("div");
    box.className = "inter";

    const top = document.createElement("div");
    top.className = "topic-top";
    top.innerHTML = '<span class="inter-kind">' + KIND_LABEL[it.kind] + '</span>';
    const tools = document.createElement("div");
    tools.className = "topic-tools";
    tools.append(
      tool("↑", "Move up", () => move(topic.interactions, n, -1)),
      tool("↓", "Move down", () => move(topic.interactions, n, 1)),
      tool("×", "Remove", () => { topic.interactions.splice(n, 1); renderTopics(); }, "del"));
    top.appendChild(tools);
    box.appendChild(top);

    box.appendChild(field(it.question, questionPlaceholder(it.kind), (v) => (it.question = v)));

    if (DEFAULT_OPTIONS[it.kind]) {
      box.appendChild(subLab(it.kind === "emoji" ? "Reactions"
        : it.kind === "ranking" ? "Items to rank" : "Options"));
      const opts = document.createElement("div");
      opts.className = "opts";
      it.options.forEach((val, oi) => {
        const row = document.createElement("div");
        row.className = "opt-row";
        row.appendChild(field(val, it.kind === "emoji" ? "🙂" : "Option " + (oi + 1),
          (v) => (it.options[oi] = v)));
        if (it.options.length > 1) {
          row.appendChild(tool("×", "Remove", () => { it.options.splice(oi, 1); renderTopics(); }, "del"));
        }
        opts.appendChild(row);
      });
      box.appendChild(opts);
      const add = document.createElement("button");
      add.className = "mini";
      add.type = "button";
      add.textContent = it.kind === "emoji" ? "+ REACTION" : "+ OPTION";
      add.addEventListener("click", () => { it.options.push(""); renderTopics(); });
      box.appendChild(add);
    }

    if (it.kind === "slider") {
      box.appendChild(subLab("Scale ends"));
      const row = document.createElement("div");
      row.className = "settings-row";
      row.appendChild(field(it.leftLabel, "0% label — e.g. not worried", (v) => (it.leftLabel = v)));
      row.appendChild(field(it.rightLabel, "100% label — e.g. very", (v) => (it.rightLabel = v)));
      box.appendChild(row);
      box.appendChild(subLab("Readout label (optional)"));
      box.appendChild(field(it.resultLabel, "Shown under the % — else it follows the nearer end",
        (v) => (it.resultLabel = v)));
    }

    box.appendChild(subLab("Seconds on screen"));
    box.appendChild(field(it.duration, "60", (v) => (it.duration = v), "number"));
    return box;
  }

  function questionPlaceholder(kind) {
    if (kind === "wordcloud") return "Prompt — e.g. one word for the future?";
    if (kind === "ranking") return "Prompt — e.g. rank by importance";
    return "The question to ask";
  }

  function move(list, i, d) {
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    renderTopics();
  }

  function tool(glyph, title, onClick, extra) {
    const b = document.createElement("button");
    b.className = "tool" + (extra ? " " + extra : "");
    b.type = "button";
    b.title = title;
    b.textContent = glyph;
    b.addEventListener("click", onClick);
    return b;
  }

  function subLab(text) {
    const d = document.createElement("div");
    d.className = "sub-lab";
    d.textContent = text;
    return d;
  }

  // Inputs keep their own value and write back on change — the card is only
  // re-rendered when the topic list structurally changes, so typing is never
  // interrupted mid-word.
  function field(value, placeholder, onInput, type) {
    const el = document.createElement("input");
    el.className = "field";
    el.value = value === undefined || value === null ? "" : value;
    el.placeholder = placeholder;
    if (type === "number") { el.type = "number"; el.min = "1"; }
    el.addEventListener("input", () => onInput(el.value));
    return el;
  }

  // ---------------- the promos' artwork ----------------
  // The offer and the ask are the same thing twice, so they are wired from one
  // list rather than two copies that drift apart.
  const PROMO_KINDS = ["offer", "donate"];
  const promoImage = { offer: "", donate: "" };

  function promoPayload(kind) {
    return {
      headline: $("#" + kind + "-headline").value,
      body: $("#" + kind + "-body").value,
      cta: $("#" + kind + "-cta").value,
      link: $("#" + kind + "-link").value,
      linkLabel: $("#" + kind + "-linklabel").value,
      image: promoImage[kind],
    };
  }

  function paintPromoImage(kind) {
    const box = $("#" + kind + "-img");
    const name = promoImage[kind];
    box.style.backgroundImage = name ? "url(/offers/" + name + ")" : "";
    box.classList.toggle("has", Boolean(name));
  }

  PROMO_KINDS.forEach((kind) => {
    $("#" + kind + "-file").addEventListener("change", async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const note = $("#" + kind + "-img-note");
      note.textContent = "Uploading…";
      const slug = ((($("#ev-name").value || kind) + "-" + kind)
        .toLowerCase().replace(/[^a-z0-9]+/g, "-"));
      try {
        const r = await fetch("/api/offer-image?slug=" + encodeURIComponent(slug), {
          method: "POST",
          headers: { "Content-Type": file.type || "application/octet-stream" },
          body: file,
        });
        const res = await r.json();
        if (!res.ok) { note.textContent = res.error || "Couldn't upload that."; return; }
        promoImage[kind] = res.image;
        paintPromoImage(kind);
        note.textContent = "Image added — remember to save the event";
      } catch (err) {
        note.textContent = "No connection — try again.";
      }
    });
  });

  $("#add-topic").addEventListener("click", addTopic);

  // ---------------- create / edit mode ----------------
  function setMode(id, config) {
    editingId = id;
    const editing = Boolean(id);
    $("#build-title").textContent = editing ? "Edit event" : "New event";
    $("#build-sub").textContent = editing
      ? "Changes apply to rooms opened from now on — rooms already running keep their own copy."
      : "Add topics in the order you want to run them.";
    $("#save-btn").textContent = editing ? "SAVE CHANGES →" : "SAVE EVENT →";
    $("#cancel-edit").hidden = !editing;

    $("#ev-brand").value = config ? config.brand : "";
    $("#ev-name").value = config ? config.eventName : "";
    PROMO_KINDS.forEach((kind) => {
      const promo = (config && config[kind]) || {};
      $("#" + kind + "-headline").value = promo.headline || "";
      $("#" + kind + "-body").value = promo.body || "";
      $("#" + kind + "-cta").value = promo.cta || "";
      $("#" + kind + "-link").value = promo.link || "";
      $("#" + kind + "-linklabel").value = promo.linkLabel || "";
      promoImage[kind] = promo.image || "";
      paintPromoImage(kind);
    });
    topics = config ? config.topics.map(toModel) : [];
    renderTopics();
    $("#save-msg").textContent = "";
    $("#save-msg").className = "msg";
    $("#build").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // config shape (what the server stores) -> builder model
  function toModel(t) {
    return {
      question: t.question || "",
      threshold: (t.settings || {}).whatsNextThreshold || 10,
      interactions: (t.interactions || []).map((it) => {
        const m = {
          kind: it.kind,
          question: it.question || "",
          duration: (it.settings || {}).duration || 60,
        };
        if (it.kind === "slider") {
          m.leftLabel = it.leftLabel || "";
          m.rightLabel = it.rightLabel || "";
          m.resultLabel = it.resultLabel || "";
        }
        if (it.kind === "poll") m.options = (it.options || []).map((o) => o.label);
        if (it.kind === "emoji") m.options = (it.options || []).map((o) => o.char);
        if (it.kind === "ranking") m.options = (it.items || []).map((o) => o.label);
        return m;
      }),
    };
  }

  // builder model -> config shape
  function toPayload() {
    return {
      brand: $("#ev-brand").value,
      eventName: $("#ev-name").value,
      offer: promoPayload("offer"),
      donate: promoPayload("donate"),
      topics: topics.map((t) => ({
        question: t.question,
        settings: { whatsNextThreshold: t.threshold },
        interactions: t.interactions.map((it) => {
          const out = {
            kind: it.kind,
            question: it.question,
            settings: { duration: it.duration },
          };
          if (it.kind === "poll") out.options = it.options.map((label) => ({ label }));
          if (it.kind === "emoji") out.options = it.options.map((char) => ({ char }));
          if (it.kind === "ranking") out.items = it.options.map((label) => ({ label }));
          if (it.kind === "slider") {
            out.leftLabel = it.leftLabel;
            out.rightLabel = it.rightLabel;
            out.resultLabel = it.resultLabel;
          }
          return out;
        }),
      })),
    };
  }

  $("#cancel-edit").addEventListener("click", () => setMode(null, null));

  $("#save-btn").addEventListener("click", async () => {
    const msg = $("#save-msg");
    msg.className = "msg";
    msg.textContent = "";

    const url = editingId ? "/api/events/" + encodeURIComponent(editingId) : "/api/events";
    const res = await post(url, toPayload());
    if (!res.ok) { msg.classList.add("err"); msg.textContent = res.error; return; }

    const wasEditing = Boolean(editingId);
    setMode(null, null);
    msg.classList.add("ok");
    msg.textContent = wasEditing
      ? 'Updated "' + res.eventName + '".'
      : 'Saved "' + res.eventName + '" — pick it under Go live.';
    await loadEvents(res.eventId);
  });

  // ---------------- launch list ----------------
  async function loadEvents(selectId) {
    const data = await get("/api/events");
    defaultEventId = data.defaultId;
    const host = $("#event-list");
    host.innerHTML = "";
    if (selectId) selectedEvent = selectId;
    if (!data.events.some((e) => e.id === selectedEvent)) {
      selectedEvent = data.events.length ? data.events[0].id : null;
    }

    data.events.forEach((e) => {
      const row = document.createElement("div");
      row.className = "event-opt" + (e.id === selectedEvent ? " sel" : "");

      const pick = document.createElement("button");
      pick.className = "event-pick";
      pick.type = "button";
      pick.innerHTML = '<div class="en"></div><div class="eb"></div><div class="ek"></div>';
      pick.querySelector(".en").textContent = e.eventName;
      pick.querySelector(".eb").textContent = e.brand;
      const summary = e.topicCount + (e.topicCount === 1 ? " topic" : " topics");
      pick.querySelector(".ek").textContent = e.kinds.length
        ? summary + " · " + e.kinds.map((k) => KIND_LABEL[k] || k).join(" · ")
        : summary + " · discussion only";
      pick.addEventListener("click", () => { selectedEvent = e.id; confirmingDelete = null; loadEvents(); });
      row.appendChild(pick);

      const acts = document.createElement("div");
      acts.className = "event-acts";
      acts.appendChild(tool("✎", "Edit this event", () => editEvent(e.id)));
      if (e.id !== defaultEventId) {
        const confirming = confirmingDelete === e.id;
        const del = tool(confirming ? "✓" : "×",
          confirming ? "Click to confirm deletion" : "Delete this event",
          () => (confirming ? doDelete(e.id) : askDelete(e.id)), "del");
        if (confirming) del.classList.add("confirming");
        acts.appendChild(del);
      }
      row.appendChild(acts);

      if (confirmingDelete === e.id) {
        const warn = document.createElement("div");
        warn.className = "del-warn";
        warn.textContent = "Delete this event? Click ✓ to confirm. Rooms already running are unaffected.";
        row.appendChild(warn);
      }

      host.appendChild(row);
    });
  }

  function askDelete(id) { confirmingDelete = id; loadEvents(); }

  async function doDelete(id) {
    const res = await del("/api/events/" + encodeURIComponent(id));
    confirmingDelete = null;
    const msg = $("#launch-msg");
    msg.className = "msg";
    if (!res.ok) { msg.classList.add("err"); msg.textContent = res.error; loadEvents(); return; }
    if (editingId === id) setMode(null, null);
    msg.classList.add("ok");
    msg.textContent = "Event deleted.";
    await loadEvents();
  }

  async function editEvent(id) {
    const res = await get("/api/events/" + encodeURIComponent(id));
    if (!res.ok) return;
    confirmingDelete = null;
    setMode(id, res.config);
    loadEvents();
  }

  async function loadRooms() {
    const data = await get("/api/rooms");
    mailConfigured = Boolean(data.mailConfigured);
    const host = $("#rooms-open");
    if (!data.rooms.length) { host.innerHTML = ""; return; }
    host.innerHTML = '<div class="rl">Rooms</div>';
    data.rooms.forEach((r) => {
      const row = document.createElement("div");
      row.className = "room-row" + (r.closed ? " off" : "");

      const a = document.createElement("a");
      a.className = "room-chip";
      a.href = "/moderator?room=" + encodeURIComponent(r.code);
      a.target = "_blank";
      a.innerHTML = "<b></b> · <span></span>";
      a.querySelector("b").textContent = r.code;
      a.querySelector("span").textContent = r.eventName + (r.closed ? " · off" : "");
      row.appendChild(a);

      // sign-ups collected in this room, downloadable as JSON
      const recap = document.createElement("a");
      recap.className = "room-leads";
      recap.href = "/recap?room=" + encodeURIComponent(r.code);
      recap.target = "_blank";
      recap.title = "Open the public debrief page for this room";
      recap.textContent = "↗ recap";
      row.appendChild(recap);

      const leads = document.createElement("a");
      leads.className = "room-leads" + (r.leads ? " has" : "");
      leads.href = "/api/leads/" + encodeURIComponent(r.code) + ".json";
      leads.title = r.leads
        ? "Download the " + r.leads + " email sign-up(s) from this room"
        : "No sign-ups yet";
      leads.textContent = "✉ " + (r.leads || 0);
      row.appendChild(leads);

      // people who raised a hand for the offer — a separate list to the
      // debrief sign-ups, and downloadable the same way
      [["interest", "\uD83D\uDCBC", "tapped the offer"],
       ["donations", "\u2764\uFE0F", "said they'd give"]].forEach(([key, icon, what]) => {
        if (!r[key]) return;
        const want = document.createElement("a");
        want.className = "room-leads has";
        want.href = "/api/interest/" + encodeURIComponent(r.code) + ".json";
        want.title = "Download the " + r[key] + " person(s) who " + what + " in this room";
        want.textContent = icon + " " + r[key];
        row.appendChild(want);
      });

      const toggle = document.createElement("button");
      toggle.className = "room-toggle" + (r.closed ? " on" : "");
      toggle.type = "button";
      toggle.textContent = r.closed ? "TURN ON" : "TURN OFF";
      toggle.title = r.closed
        ? "Reopen this room — its tallies are still there"
        : "Close this room: phones stop voting, tallies are kept";
      toggle.addEventListener("click", async () => {
        await post("/api/rooms/" + encodeURIComponent(r.code), { closed: !r.closed });
        loadRooms();
      });
      row.appendChild(toggle);

      // wiping a rehearsal is destructive, so it takes two taps
      const confirming = confirmingReset === r.code;
      const reset = document.createElement("button");
      reset.className = "room-toggle reset" + (confirming ? " confirming" : "");
      reset.type = "button";
      reset.textContent = confirming ? "CONFIRM WIPE" : "RESET";
      reset.title = "Clear every vote, tally and challenge in this room";
      reset.addEventListener("click", async () => {
        if (!confirming) { confirmingReset = r.code; loadRooms(); return; }
        confirmingReset = null;
        await post("/api/rooms/" + encodeURIComponent(r.code), { reset: true });
        const msg = $("#launch-msg");
        msg.className = "msg ok";
        msg.textContent = "Room " + r.code + " wiped — every tally back to zero.";
        loadRooms();
      });
      row.appendChild(reset);

      // sending the debrief reaches real inboxes, so it takes two clicks
      if (r.leads) {
        const sendingNow = confirmingSend === r.code;
        const send = document.createElement("button");
        send.className = "room-toggle send" + (sendingNow ? " confirming" : "");
        send.type = "button";
        send.disabled = !mailConfigured || !r.unsent;
        send.textContent = !r.unsent ? "SENT"
          : sendingNow ? "CONFIRM SEND" : "SEND DEBRIEF (" + r.unsent + ")";
        send.title = !mailConfigured
          ? "No mail server configured — set SMTP_HOST and MAIL_FROM"
          : !r.unsent ? "Everyone on this list has already had the debrief"
          : "Email the recap link to the " + r.unsent + " who signed up";
        send.addEventListener("click", async () => {
          if (!sendingNow) { confirmingSend = r.code; loadRooms(); return; }
          confirmingSend = null;
          const msg = $("#launch-msg");
          msg.className = "msg";
          msg.textContent = "Sending…";
          const res = await post("/api/debrief/" + encodeURIComponent(r.code), {});
          msg.className = "msg " + (res.ok ? "ok" : "err");
          msg.textContent = res.ok
            ? "Debrief sent to " + res.sent + (res.sent === 1 ? " person" : " people") +
              (res.skipped ? " (" + res.skipped + " already had it)" : "")
            : (res.error || ("Sent " + res.sent + ", failed " + res.failed +
                             (res.errors && res.errors.length ? " — " + res.errors[0] : "")));
          loadRooms();
        });
        row.appendChild(send);
      }

      if (confirming) {
        const warn = document.createElement("div");
        warn.className = "del-warn";
        warn.textContent = "Wipe every vote, tally and challenge in " + r.code +
          "? The event stays; only the numbers are cleared.";
        row.appendChild(warn);
      }
      if (confirmingSend === r.code) {
        const warn = document.createElement("div");
        warn.className = "del-warn";
        warn.textContent = "Email the debrief to " + r.unsent + " " +
          (r.unsent === 1 ? "person" : "people") + " who signed up in " + r.code +
          "? This goes to their real inboxes.";
        row.appendChild(warn);
      }

      host.appendChild(row);
    });
  }

  const codeInput = $("#launch-code");
  codeInput.addEventListener("input", () => {
    codeInput.value = codeInput.value.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
  });

  $("#launch-btn").addEventListener("click", async () => {
    const msg = $("#launch-msg");
    msg.className = "msg";
    msg.textContent = "";
    $("#launch-links").hidden = true;

    if (!selectedEvent) { msg.classList.add("err"); msg.textContent = "Create an event first."; return; }
    const code = codeInput.value.trim();
    if (!code) { msg.classList.add("err"); msg.textContent = "Enter a room code."; return; }

    const res = await post("/api/rooms", { code, eventId: selectedEvent });
    if (!res.ok) { msg.classList.add("err"); msg.textContent = res.error; return; }

    const q = "?room=" + encodeURIComponent(res.code);
    $("#links-code").textContent = res.code;
    $("#link-audience").href = "/" + q;
    $("#link-moderator").href = "/moderator" + q;
    $("#link-projector").href = "/projector" + q;
    $("#launch-links").hidden = false;
    msg.classList.add("ok");
    msg.textContent = "Room " + res.code + " is live.";
    codeInput.value = "";
    loadRooms();
  });

  // ---------------- helpers ----------------
  // the passcode session can expire under us (server restart) — send the crew
  // back to the login screen rather than failing quietly
  function checkAuth(r) {
    if (r.status === 401) { location.href = "/login?next=/setup"; return false; }
    return true;
  }

  async function get(url) {
    const r = await fetch(url, { headers: { "Cache-Control": "no-store" } });
    if (!checkAuth(r)) return { ok: false, events: [], rooms: [] };
    return r.json();
  }
  async function send(url, method, body) {
    try {
      const r = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!checkAuth(r)) return { ok: false, error: "Signing in…" };
      return await r.json();
    } catch (e) {
      return { ok: false, error: "Could not reach the server." };
    }
  }
  const post = (url, body) => send(url, "POST", body);
  const del = (url) => send(url, "DELETE");

  renderTopics();
  loadEvents();
  loadRooms();
})();
