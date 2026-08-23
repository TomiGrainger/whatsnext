// The contact address and the date come from the server, so this page can never
// drift out of step with the address that actually sends the mail.
(function () {
  fetch("/api/privacy-meta")
    .then((r) => r.json())
    .then((d) => {
      if (d.contact) document.getElementById("pv-email").textContent = d.contact;
      if (d.updated) document.getElementById("pv-date").textContent = d.updated;
    })
    .catch(() => { /* the fallback text in the page is already true enough */ });
})();
