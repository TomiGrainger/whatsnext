// Table cards and a door poster for one room. Everything here is drawn from
// the room's own join URL, so a printed card can never point at the wrong place.
(function () {
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const roomInput = $("#room");
  const titleInput = $("#title");
  roomInput.value = (params.get("room") || "WN25").toUpperCase();

  const TENTS = 4;

  async function load() {
    const code = roomInput.value.trim().toUpperCase() || "WN25";
    const r = await fetch("/api/state?room=" + encodeURIComponent(code));
    const st = await r.json();
    const warn = $("#warn");
    if (st.exists === false) {
      warn.hidden = false;
      warn.textContent = "Room " + code + " isn't open yet. The code below still works — "
        + "it just needs the room opening from setup before anyone can join.";
    } else {
      warn.hidden = true;
      if (!titleInput.value) titleInput.value = st.eventName || "";
    }
    paint(code, st.joinUrl || (location.origin + "/" + code), st.brand || "THE UPGRADE");
  }

  function paint(code, url, brand) {
    const pretty = url.replace(/^https?:\/\//, "");
    const qr = "/qr.svg?light=white&url=" + encodeURIComponent(url);

    $("#poster-brand").innerHTML = "";
    $("#poster-brand").append(document.createTextNode(brand + " "));
    const em = document.createElement("em");
    em.textContent = "LIVE";
    $("#poster-brand").appendChild(em);

    $("#poster-event").textContent = titleInput.value || "";
    $("#poster-qr").src = qr;
    $("#poster-url").textContent = pretty;
    $("#poster-code").textContent = code;

    const host = $("#tents");
    host.innerHTML = "";
    for (let i = 0; i < TENTS; i++) {
      const t = document.createElement("div");
      t.className = "tent";
      const b = document.createElement("div");
      b.className = "t-brand";
      b.append(document.createTextNode(brand + " "));
      const e = document.createElement("em");
      e.textContent = "LIVE";
      b.appendChild(e);
      const img = document.createElement("img");
      img.className = "t-qr";
      img.src = qr;
      img.alt = "";
      const u = document.createElement("div");
      u.className = "t-url";
      u.textContent = pretty;
      const n = document.createElement("div");
      n.className = "t-note";
      n.textContent = "Point your camera at the code";
      t.append(b, img, u, n);
      host.appendChild(t);
    }
  }

  roomInput.addEventListener("change", load);
  titleInput.addEventListener("input", () => {
    $("#poster-event").textContent = titleInput.value || "";
  });
  $("#print-btn").addEventListener("click", () => window.print());
  load();
})();
