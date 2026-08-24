// Audience surface controller.
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  let joined = sessionStorage.getItem("upgrade_joined") === "1";
  let checkedIn = sessionStorage.getItem("upgrade_checkedin") === "1";
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
  function enterRoom(code) {
    Live.setRoom(code || "WN25");
    joined = true;
    sessionStorage.setItem("upgrade_joined", "1");
    sessionStorage.setItem("upgrade_room", Live.room());
    Live.send("join");
    buildCheckIn();
    render(Live.get(), true);
  }

  $("#join-btn").addEventListener("click", () => enterRoom(codeInput.value.trim()));


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

  // ---- your profile ----
  // Kept on the phone as well as the server so the sheet is filled in when you
  // reopen it, and so the header avatar survives a reload.
  const ME_KEY = "upgrade_me";
  let me = {};
  try { me = JSON.parse(localStorage.getItem(ME_KEY) || "{}"); } catch (e) { me = {}; }

  function initialsOf(name) {
    const parts = (name || "").split(" ").filter(Boolean);
    if (!parts.length) return "+";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function paintMe() {
    const badge = $("#me-btn");
    const ini = initialsOf(me.name);
    const has = Boolean(me.name || me.avatar);
    $("#me-initials").textContent = me.avatar ? "" : ini;
    $("#me-photo-initials").textContent = me.name ? ini : "?";
    badge.classList.toggle("has", has);
    const url = me.avatar ? "/avatars/" + me.avatar : "";
    badge.style.backgroundImage = url ? "url(" + url + ")" : "";
    $("#me-photo").style.backgroundImage = url ? "url(" + url + ")" : "";
    $("#me-photo").classList.toggle("has", Boolean(url));
    $("#me-name").value = me.name || "";
    $("#me-occupation").value = me.occupation || "";
    $("#me-fact").value = me.fact || "";
    $("#me-email").value = me.email || "";
    $("#me-link").value = me.link || "";
    $("#me-shared").checked = Boolean(me.shared);
    $("#me-contact").hidden = !me.shared;
    $("#me-note").textContent = me.shared
      ? "Your name, job, fact and photo are visible to the room. Contact details only go to people you accept."
      : "Only the crew sees this, unless they put you on the big screen.";

    // the prompt shouts until there's a profile, then settles into a summary
    const cta = $("#profile-cta");
    cta.classList.toggle("done", has);
    $("#pc-initials").textContent = has ? (me.avatar ? "" : ini) : "👋";
    $("#pc-face").style.backgroundImage = url ? "url(" + url + ")" : "";
    $("#pc-face").classList.toggle("has", Boolean(url));
    $("#pc-title").textContent = has ? (me.name || "YOUR PROFILE") : "ADD YOUR PROFILE";
    $("#pc-sub").textContent = has
      ? (me.shared ? "Visible in the room directory · tap to edit"
                   : "Only the crew can see it · tap to edit")
      : "So the room knows who you are";
  }

  $("#me-shared").addEventListener("change", (e) => {
    $("#me-contact").hidden = !e.target.checked;
  });

  function openMe() {
    paintMe();
    $("#me-sheet").classList.add("show");
    $("#me-scrim").classList.add("show");
  }
  function closeMe() {
    $("#me-sheet").classList.remove("show");
    $("#me-scrim").classList.remove("show");
  }
  $("#me-btn").addEventListener("click", openMe);
  $("#profile-cta").addEventListener("click", openMe);
  $("#me-x").addEventListener("click", closeMe);
  $("#me-scrim").addEventListener("click", closeMe);

  $("#me-save").addEventListener("click", async () => {
    me.name = $("#me-name").value.trim();
    me.occupation = $("#me-occupation").value.trim();
    me.fact = $("#me-fact").value.trim();
    me.shared = $("#me-shared").checked;
    me.email = me.shared ? $("#me-email").value.trim() : "";
    me.link = me.shared ? $("#me-link").value.trim() : "";
    localStorage.setItem(ME_KEY, JSON.stringify(me));
    Live.send("profile", {
      name: me.name, occupation: me.occupation, fact: me.fact,
      shared: me.shared, email: me.email, link: me.link,
    });
    paintMe();
    closeMe();
    UI.toast(me.shared ? "Profile saved and shared" : "Profile saved");
    refreshMine();
  });

  // Two taps, because it can't be undone.
  let forgetArmed = false;
  $("#me-forget").addEventListener("click", async () => {
    const btn = $("#me-forget");
    if (!forgetArmed) {
      forgetArmed = true;
      btn.classList.add("arm");
      btn.textContent = "Tap again to delete everything";
      setTimeout(() => {
        forgetArmed = false;
        btn.classList.remove("arm");
        btn.textContent = "Delete my details";
      }, 5000);
      return;
    }
    await Live.send("forgetMe", {});
    me = {};
    localStorage.removeItem(ME_KEY);
    sessionStorage.removeItem("upgrade_checkedin");
    checkedIn = false;
    vibeChoice = null;
    paintMe();
    closeMe();
    UI.toast("Your details are gone");
    render(Live.get(), true);
  });

  // The photo goes up as the raw file body — the server identifies it by its
  // bytes rather than trusting the name or the type we claim.
  $("#me-file").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const note = $("#me-photo-note");
    if (file.size > 3 * 1024 * 1024) {
      note.textContent = "That photo is too big — keep it under 3MB.";
      note.classList.add("err");
      return;
    }
    note.classList.remove("err");
    note.textContent = "Uploading…";
    try {
      const r = await fetch("/api/avatar?room=" + encodeURIComponent(Live.room()) +
                            "&pid=" + encodeURIComponent(Live.pid()), {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      const res = await r.json();
      if (!res.ok) {
        note.textContent = res.error || "Couldn't upload that.";
        note.classList.add("err");
        return;
      }
      me.avatar = res.avatar;
      localStorage.setItem(ME_KEY, JSON.stringify(me));
      paintMe();
      note.textContent = "Photo added";
    } catch (err) {
      note.textContent = "No connection — try again.";
      note.classList.add("err");
    }
  });

  paintMe();

  // ---- check in ----
  // Everyone says who they are on the way in. Three taps: it is the difference
  // between an anonymous crowd and a room the moderator can actually read.
  let vibeChoice = null;
  let optionsLoaded = null;

  function loadOptions() {
    if (!optionsLoaded) {
      optionsLoaded = fetch("/api/onboarding")
        .then((r) => r.json())
        .catch(() => ({ occupations: [], vibes: [] }));
    }
    return optionsLoaded;
  }

  async function buildCheckIn() {
    const opts = await loadOptions();
    const sel = $("#ci-occ");
    if (!sel.options.length) {
      const first = document.createElement("option");
      first.value = "";
      first.textContent = "Choose one\u2026";
      sel.appendChild(first);
      opts.occupations.forEach((label) => {
        const o = document.createElement("option");
        o.value = label;
        o.textContent = label;
        sel.appendChild(o);
      });
      const other = document.createElement("option");
      other.value = "__other";
      other.textContent = "Something else\u2026";
      sel.appendChild(other);
    }
    const host = $("#ci-vibes");
    if (!host.children.length) {
      opts.vibes.forEach((v) => {
        const b = document.createElement("button");
        b.className = "ci-vibe";
        b.type = "button";
        b.dataset.vibe = v.id;
        b.setAttribute("aria-pressed", "false");
        const e = document.createElement("span");
        e.className = "e";
        e.textContent = v.char;
        const l = document.createElement("span");
        l.className = "l";
        l.textContent = v.label;
        b.append(e, l);
        b.addEventListener("click", () => {
          vibeChoice = v.id;
          $$(".ci-vibe").forEach((x) => {
            const on = x.dataset.vibe === v.id;
            x.classList.toggle("on", on);
            x.setAttribute("aria-pressed", String(on));
          });
          checkInReady();
        });
        host.appendChild(b);
      });
    }
    // Someone who has been before shouldn't retype what hasn't changed: their
    // phone still has last time's answers, so check-in becomes two taps.
    if (me.name) $("#ci-name").value = me.name;
    if (me.occupation) {
      const known = Array.from(sel.options).some((o) => o.value === me.occupation);
      if (known) {
        sel.value = me.occupation;
      } else {
        sel.value = "__other";
        $("#ci-occ-other").hidden = false;
        $("#ci-occ-other").value = me.occupation;
      }
    }
    if (me.vibe) {
      vibeChoice = me.vibe;
      $$(".ci-vibe").forEach((x) => {
        const on = x.dataset.vibe === me.vibe;
        x.classList.toggle("on", on);
        x.setAttribute("aria-pressed", String(on));
      });
    }
    if (me.name && me.occupation && me.vibe) {
      $("#ci-title").textContent = "STILL YOU?";
      $("#ci-sub").textContent = "We remembered last time. Change anything that's moved on.";
    }
    checkInReady();
  }

  function checkInOccupation() {
    const sel = $("#ci-occ").value;
    if (sel === "__other") return $("#ci-occ-other").value.trim();
    return sel;
  }

  function checkInReady() {
    const ok = Boolean($("#ci-name").value.trim() && checkInOccupation() && vibeChoice);
    $("#ci-done").disabled = !ok;
  }

  $("#ci-occ").addEventListener("change", () => {
    const other = $("#ci-occ").value === "__other";
    $("#ci-occ-other").hidden = !other;
    if (other) $("#ci-occ-other").focus();
    checkInReady();
  });
  $("#ci-name").addEventListener("input", checkInReady);
  $("#ci-occ-other").addEventListener("input", checkInReady);

  $("#ci-done").addEventListener("click", () => {
    me.name = $("#ci-name").value.trim();
    me.occupation = checkInOccupation();
    me.vibe = vibeChoice;
    localStorage.setItem(ME_KEY, JSON.stringify(me));
    // `checkin` is what marks them as counted in the room's make-up
    Live.send("profile", {
      name: me.name, occupation: me.occupation, vibe: me.vibe,
      fact: me.fact || "", shared: Boolean(me.shared),
      email: me.email || "", link: me.link || "", checkin: true,
    });
    checkedIn = true;
    sessionStorage.setItem("upgrade_checkedin", "1");
    paintMe();
    render(Live.get(), true);
  });

  // ---- the lobby ----
  // From checking in until the crew starts. The room filling up beats a
  // countdown: it is about them, and it makes a half-empty room feel full.
  function paintLobby(st) {
    const rs = st.roomStats;
    if (!rs) return;
    $("#lb-n").textContent = rs.checkedIn;
    $("#lb-n-lab").textContent = rs.checkedIn === 1
      ? "here so far — you're the first" : "here so far";

    $("#lb-occ-block").hidden = !rs.occupations.length;
    const top = Math.max(1, ...rs.occupations.map((o) => o.count));
    const host = $("#lb-occ");
    host.innerHTML = "";
    rs.occupations.slice(0, 6).forEach((o) => {
      const row = document.createElement("div");
      row.className = "lb-bar";
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

    const vibes = rs.vibes.filter((v) => v.count > 0).sort((a, b) => b.count - a.count);
    $("#lb-vibe-block").hidden = !vibes.length;
    const vhost = $("#lb-vibes");
    vhost.innerHTML = "";
    vibes.forEach((v) => {
      const chip = document.createElement("div");
      chip.className = "lb-vibe";
      const e = document.createElement("span");
      e.className = "e";
      e.textContent = v.char;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = v.count;
      chip.append(e, n);
      vhost.appendChild(chip);
    });

    const order = st.runningOrder || [];
    $("#lb-order-block").hidden = !order.length;
    const ohost = $("#lb-order");
    if (ohost.dataset.stamp !== order.join("|")) {
      ohost.dataset.stamp = order.join("|");
      ohost.innerHTML = "";
      order.forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q;
        ohost.appendChild(li);
      });
    }
  }

  // ---- the offer ----
  // Raising a hand keeps you in the room: it records interest against your
  // phone and, where we already have an address, needs nothing typed at all.
  let offerSeen = null;          // which promo we last auto-opened for
  const interested = {};         // kind -> have they already raised a hand

  function offerImageUrl(o) { return o && o.image ? "/offers/" + o.image : ""; }

  function paintOffer(st) {
    const o = st.promo;                       // the one on screen, if any
    // st.promo is only ever populated when a promo screen is actually up, so
    // its presence is the whole condition. (This read st.promoLive, a field
    // that stopped existing when the takeovers were unified — which silently
    // disabled the sheet on every phone.)
    const live = Boolean(o && !st.closed);

    if (o) {
      const img = offerImageUrl(o);
      const hero = $("#offer-hero");
      if (img) { hero.src = img; hero.hidden = false; } else { hero.hidden = true; }
      $("#offer-headline").textContent = o.headline;
      $("#offer-body").textContent = o.body || "";
      $("#offer-body").hidden = !o.body;
      // The ask is one button that goes where the giving happens. The offer is
      // two: read more, or leave an address — different intentions, and asking
      // someone to choose between a button and a footnote link buried one.
      const linkBtn = $("#offer-link");
      linkBtn.textContent = o.link ? (o.linkLabel || "See the details") : "";
      linkBtn.href = o.link || "#";
      linkBtn.hidden = !o.link;
      linkBtn.className = o.opensLink ? "btn" : "btn ghost";

      // the offer's primary is leaving an address; reading more is the quieter
      // of the two, so the weights say which one you actually want
      const emailBtn = $("#offer-cta");
      emailBtn.textContent = o.cta;
      emailBtn.className = "btn";
      emailBtn.hidden = Boolean(o.opensLink);

      const done = $("#offer-link-2");
      done.textContent = o.link ? (o.linkLabel || "See the details") : "";
      done.href = o.link || "#";
      done.hidden = !o.link;
    }
    paintInterested();
    paintClosingPromos(st);

    // the moderator putting it up opens it once; dismissing it stays dismissed
    const key = o ? o.kind + ":" + o.headline : null;
    if (joined && live && offerSeen !== key) {
      offerSeen = key;
      openOffer();
    }
    if (!live && offerSeen) { offerSeen = null; closeOffer(); }
  }

  // On the closing screen every promo the event has just sits there, no
  // moderator needed — it is the last thing on the phone for the rest of the night.
  function paintClosingPromos(st) {
    const promos = Object.values(st.promos || {});
    const host = $("#cv-promos");
    const stamp = promos.map((p) => p.kind + p.headline).join("|") + "/" + doneList().join(",");
    if (host.dataset.stamp === stamp) return;
    host.dataset.stamp = stamp;
    host.innerHTML = "";
    promos.forEach((p) => {
      const box = document.createElement("div");
      box.className = "cv-offer";
      if (p.image) {
        const img = document.createElement("img");
        img.className = "offer-hero";
        img.src = offerImageUrl(p);
        img.alt = "";
        box.appendChild(img);
      }
      const h = document.createElement("div");
      h.className = "cv-offer-head";
      h.textContent = p.headline;
      box.appendChild(h);
      if (p.body) {
        const b = document.createElement("div");
        b.className = "cv-offer-body";
        b.textContent = p.body;
        box.appendChild(b);
      }
      const done = interested[p.kind];
      // the closing screen mirrors the sheet: the ask is a link, the offer is
      // a link plus a way to leave an address
      if (p.link) {
        const a = document.createElement("a");
        a.className = p.opensLink ? "btn" : "btn ghost";
        a.href = p.link;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = p.linkLabel || "See the details";
        if (p.opensLink) a.addEventListener("click", () => noteInterest(p));
        box.appendChild(a);
      }
      if (!p.opensLink) {
        const btn = document.createElement("button");
        btn.className = "btn";
        btn.disabled = done;
        btn.textContent = done ? "\u2713 YOU'RE ON THE LIST" : p.cta;
        btn.addEventListener("click", () => { openOffer(p); raiseHand("#offer-email", p); });
        box.appendChild(btn);
      }
      host.appendChild(box);
    });
  }

  function doneList() {
    return Object.keys(interested).filter((k) => interested[k]);
  }

  function paintInterested() {
    const o = (Live.get() || {}).promo;
    const done = Boolean(o && interested[o.kind]);
    $("#offer-ask").hidden = done;
    $("#offer-done").hidden = !done;
  }

  function openOffer() {
    $("#offer-sheet").classList.add("show");
    $("#offer-scrim").classList.add("show");
  }
  function closeOffer() {
    $("#offer-sheet").classList.remove("show");
    $("#offer-scrim").classList.remove("show");
  }
  $("#offer-x").addEventListener("click", closeOffer);
  $("#offer-scrim").addEventListener("click", closeOffer);

  // Record a tap without asking for anything: the link is already taking them
  // somewhere, so a form would be in the way.
  async function noteInterest(o) {
    if (interested[o.kind]) return;
    interested[o.kind] = true;
    const known = (me && me.email) || knownLeadEmail || "";
    await Live.send("interested", { email: known, name: (me && me.name) || "" });
    paintInterested();
    paintClosingPromos(Live.get() || {});
  }

  async function raiseHand(emailField, promo) {
    const o = promo || (Live.get() || {}).promo;
    if (!o) return;
    // if we have no address for them, ask for one rather than guessing
    const known = (me && me.email) || knownLeadEmail;
    const typed = emailField ? $(emailField).value.trim() : "";
    if (!known && !typed) {
      $("#offer-email-wrap").hidden = false;
      $("#offer-email").focus();
      openOffer();
      return;
    }
    const res = await Live.send("interested", { email: typed || known, name: (me && me.name) || "" });
    if (res && res.saved === false) {
      // never show a tick for a lead that isn't written down
      UI.toast("That didn't save — try once more");
      return;
    }
    interested[o.kind] = true;
    paintInterested();
    paintClosingPromos(Live.get() || {});
    // a donation happens somewhere else, so the button takes them there as
    // well as recording the tap
    if (o.opensLink && o.link) window.open(o.link, "_blank", "noopener");
    UI.toast(o.kind === "donate" ? "Thank you" : "You're on the list");
    refreshMine();
  }

  $("#offer-cta").addEventListener("click", () => raiseHand("#offer-email"));
  // the link button records too — it is the only thing the ask offers, so a
  // tap on it is the whole signal
  $("#offer-link").addEventListener("click", () => {
    const o = (Live.get() || {}).promo;
    if (o && o.opensLink) noteInterest(o);
  });

  // ---- who's in the room ----
  // The snapshot carries the directory but never contact details; those live
  // behind /api/me and only appear once someone accepts a request.
  let mine = { incoming: [], pending: [], connections: [], shared: false };
  let roomTab = "all";
  let minePoll = null;

  async function refreshMine() {
    try {
      const r = await fetch("/api/me?room=" + encodeURIComponent(Live.room()) +
                            "&pid=" + encodeURIComponent(Live.pid()),
                            { headers: { "Cache-Control": "no-store" } });
      const d = await r.json();
      if (d.exists) {
        mine = d;
        (d.interested || []).forEach((k) => { interested[k] = true; });
        paintRoom(Live.get());
        paintInterested();
      }
    } catch (e) { /* offline — the sheet just shows what it had */ }
  }

  function openRoom() {
    $("#room-sheet").classList.add("show");
    $("#room-scrim").classList.add("show");
    refreshMine();
    // requests can arrive while the sheet is open
    if (!minePoll) minePoll = setInterval(refreshMine, 4000);
  }
  function closeRoom() {
    $("#room-sheet").classList.remove("show");
    $("#room-scrim").classList.remove("show");
    clearInterval(minePoll);
    minePoll = null;
  }
  $("#room-bar").addEventListener("click", openRoom);
  $("#room-x").addEventListener("click", closeRoom);
  $("#room-scrim").addEventListener("click", closeRoom);
  $("#room-optin-btn").addEventListener("click", () => { closeRoom(); openMe(); });
  $$(".room-tabs .rt").forEach((b) => b.addEventListener("click", () => {
    roomTab = b.dataset.tab;
    $$(".room-tabs .rt").forEach((x) => x.classList.toggle("on", x === b));
    paintRoom(Live.get());
  }));

  function personRow(p, opts) {
    const row = document.createElement("div");
    row.className = "person";

    const face = document.createElement("div");
    face.className = "p-face";
    if (p.avatar) face.style.backgroundImage = "url(/avatars/" + p.avatar + ")";
    else face.textContent = p.initials || "?";

    const body = document.createElement("div");
    body.className = "p-body";
    const nm = document.createElement("div");
    nm.className = "p-name";
    nm.textContent = p.name || "Someone";
    body.appendChild(nm);
    if (p.occupation) {
      const oc = document.createElement("div");
      oc.className = "p-occ";
      oc.textContent = p.occupation;
      body.appendChild(oc);
    }
    if (p.fact) {
      const ft = document.createElement("div");
      ft.className = "p-fact";
      ft.textContent = "“" + p.fact + "”";
      body.appendChild(ft);
    }
    // contact details appear only for accepted connections
    if (opts.contact && (p.email || p.link)) {
      const box = document.createElement("div");
      box.className = "p-contact";
      if (p.email) {
        const a = document.createElement("a");
        a.href = "mailto:" + p.email;
        a.textContent = p.email;
        box.appendChild(a);
      }
      if (p.link) {
        const a = document.createElement("a");
        const href = /^https?:\/\//i.test(p.link) ? p.link : "https://" + p.link;
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener noreferrer nofollow";
        a.textContent = p.link;
        box.appendChild(a);
      }
      const save = document.createElement("button");
      save.className = "p-save";
      save.textContent = "SAVE TO CONTACTS";
      save.addEventListener("click", () => downloadVcard(p));
      box.appendChild(save);
      body.appendChild(box);
    }

    row.append(face, body);
    if (opts.action) row.appendChild(opts.action);
    return row;
  }

  // A vCard is just text — the phone opens it in Contacts.
  function downloadVcard(p) {
    const esc = (s) => (s || "").replace(/[\\;,]/g, (m) => "\\" + m).replace(/\n/g, " ");
    const lines = [
      "BEGIN:VCARD", "VERSION:3.0",
      "FN:" + esc(p.name || "Guest"),
      p.occupation ? "TITLE:" + esc(p.occupation) : "",
      p.email ? "EMAIL;TYPE=INTERNET:" + esc(p.email) : "",
      p.link ? "URL:" + esc(p.link) : "",
      "NOTE:" + esc("Met at " + ((Live.get() || {}).eventName || "the event") +
                    (p.fact ? " — " + p.fact : "")),
      "END:VCARD",
    ].filter(Boolean);
    const blob = new Blob([lines.join("\r\n")], { type: "text/vcard" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (p.name || "contact").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".vcf";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function paintRoom(st) {
    if (!st) return;
    const dir = (st.directory || []).filter((p) => p.pid !== Live.pid());
    $("#room-count").textContent = dir.length ? "· " + dir.length : "";
    $("#conn-count").textContent = mine.connections.length ? "· " + mine.connections.length : "";

    // not opted in? explain rather than showing them the room
    const optedIn = Boolean(mine.shared);
    $("#room-optin").hidden = optedIn;
    $("#room-body").hidden = !optedIn;
    if (!optedIn) return;

    // requests waiting on you, at the top
    const reqs = $("#room-reqs");
    reqs.innerHTML = "";
    mine.incoming.forEach((req) => {
      const yes = document.createElement("button");
      yes.className = "p-act yes";
      yes.textContent = "ACCEPT";
      yes.addEventListener("click", async () => {
        await Live.send("connectRespond", { id: req.id, accept: true });
        UI.toast("Connected — details swapped");
        refreshMine();
      });
      const no = document.createElement("button");
      no.className = "p-act no";
      no.textContent = "IGNORE";
      no.addEventListener("click", async () => {
        await Live.send("connectRespond", { id: req.id, accept: false });
        refreshMine();
      });
      const acts = document.createElement("div");
      acts.className = "p-acts";
      acts.append(yes, no);

      const row = personRow(req.who, { action: acts });
      row.classList.add("req");
      reqs.appendChild(row);
    });
    if (mine.incoming.length) {
      const lab = document.createElement("div");
      lab.className = "reqs-lab";
      lab.textContent = mine.incoming.length === 1
        ? "1 person wants to connect" : mine.incoming.length + " people want to connect";
      reqs.prepend(lab);
    }

    const list = $("#room-list");
    list.innerHTML = "";

    if (roomTab === "mine") {
      if (!mine.connections.length) {
        list.appendChild(emptyNote("No connections yet. Tap CONNECT on someone to ask."));
      }
      mine.connections.forEach((p) => list.appendChild(personRow(p, { contact: true })));
      return;
    }

    if (!dir.length) {
      list.appendChild(emptyNote("Nobody else has shared a profile yet."));
      return;
    }
    const connected = new Set(mine.connections.map((c) => c.pid));
    const asked = new Set(mine.pending);
    dir.forEach((p) => {
      let action = document.createElement("button");
      if (connected.has(p.pid)) {
        action.className = "p-act done";
        action.textContent = "CONNECTED";
        action.disabled = true;
      } else if (asked.has(p.pid)) {
        action.className = "p-act waiting";
        action.textContent = "ASKED";
        action.disabled = true;
      } else {
        action.className = "p-act";
        action.textContent = "CONNECT";
        action.addEventListener("click", async () => {
          await Live.send("connect", { to: p.pid });
          UI.toast("Request sent");
          refreshMine();
        });
      }
      list.appendChild(personRow(p, { action }));
    });
  }

  function emptyNote(text) {
    const d = document.createElement("div");
    d.className = "qa-empty";
    d.textContent = text;
    return d;
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
  async function submitWord() {
    const w = $("#wc-input").value.trim();
    if (!w) return;
    $("#wc-input").value = "";
    const res = await Live.send("word", { word: w });
    // A word the filter caught gets the same reply as one that landed: saying
    // "blocked" out loud only invites the sender to try a spelling that works.
    const left = res && typeof res.wordsLeft === "number" ? res.wordsLeft : null;
    if (left === 0) UI.toast("That's your three words");
    else UI.toast(left === 1 ? "Word added \u00b7 1 left" : "Word added");
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
    paintLobby(st);

    // choose screen
    const targetMode = st.mode;
    const screenId = !joined ? "s-join"
      : !checkedIn ? "s-checkin"
      : !st.started ? "s-lobby"
      : (MODE_SCREEN[targetMode] || "s-discussion");
    const active = $(".screen.active");
    if (!active || active.id !== screenId || force) showScreen(screenId);
    updateCounter(st);

    // topic-dependent titles
    setQuestionTitle("#disc-q", st.topic, true);
    setQuestionTitle("#res-q", st.topic, true);

    renderQa(st);
    paintRoom(st);
    paintOffer(st);

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

  // If this tab already joined a room, reconnect to it after a reload — but a
  // room named in the URL wins. Scanning a new code on a phone that was at last
  // month's event must land in this month's room.
  const savedRoom = sessionStorage.getItem("upgrade_room");
  if (joined && savedRoom && !Live.roomWasGiven()) Live.setRoom(savedRoom);
  if (joined && savedRoom && Live.roomWasGiven() && savedRoom !== Live.room()) {
    sessionStorage.setItem("upgrade_room", Live.room());
    sessionStorage.removeItem("upgrade_checkedin");
    checkedIn = false;          // a different room means checking in again
  }
  if (joined && !checkedIn) buildCheckIn();

  // back to the join screen to retype a code
  $("#nosuch-retry").addEventListener("click", () => {
    sessionStorage.removeItem("upgrade_joined");
    sessionStorage.removeItem("upgrade_checkedin");
    sessionStorage.removeItem("upgrade_room");
    location.href = "/";
  });

  // ---- closing screen: the debrief sign-up ----
  const LEAD_KEY = "upgrade_lead_done";
  let knownLeadEmail = "";
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
        // the phone's id goes with it: that is what ties the address to
        // everything this phone did tonight
        body: JSON.stringify({ email, name: $("#lead-name").value.trim(),
                               room: Live.room(), pid: Live.pid() }),
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
      knownLeadEmail = email;
      showLeadDone();
    } catch (err) {
      note.textContent = "No connection — try again in a moment.";
      note.classList.add("err");
      btn.disabled = false;
      btn.textContent = "SEND ME THE DEBRIEF →";
    }
  });

  Live.onStatus((up) => { $("#net-chip").hidden = up; });

  // Scanning the QR already said which room. Asking someone to confirm the room
  // they just scanned is a screen for nothing, so it is skipped — the join
  // screen is still there for anyone who typed the bare address.
  if (!joined && Live.roomWasGiven()) enterRoom(Live.room());

  Live.onState(render);
  Live.connect("audience");
  refreshMine();
  render(Live.get());
})();
