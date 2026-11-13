(function () {
  var toast = window.htToast || function (m) { alert(m); };
  function q(s, el) { return (el || document).querySelector(s); }
  async function post(url, body) {
    try {
      var r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      return await r.json();
    } catch (e) { return { ok: false, error: "Network error. Try again." }; }
  }
  async function refreshFeed() {
    var w = q("#feed-wrap"); if (!w) return;
    var r = await fetch("/connect/feed");
    if (r.ok) { w.innerHTML = await r.text(); if (window.htPaint) window.htPaint(); }
  }
  function applyProfile(p) {
    if (!p) return;
    q("#pc-interests").textContent = p.interests && p.interests.length ? p.interests.join(", ") : "Nothing yet. Flip a switch below.";
    q("#pc-seen").textContent = p.seen_ess;
    var n = Object.keys(p.sources).filter(function (k) { return p.sources[k].on; }).length;
    q("#pc-count").textContent = n + (n === 1 ? " source" : " sources");
    refreshFeed();
  }
  function setRow(row, state, label, err) {
    row.dataset.state = state;
    var sw = q(".switch", row);
    sw.setAttribute("aria-checked", state === "off" ? "false" : "true");
    sw.disabled = state === "busy";
    var form = q("[data-form]", row);
    if (form) {
      form.hidden = state === "off";
      var inp = q("input[name=handle]", form), btn = q("button[type=submit]", form), ch = q("[data-change]", form);
      if (inp) inp.disabled = state === "on" || state === "busy";
      var ps = q("[data-pick-save]", form); if (ps) ps.disabled = state === "busy" || form.querySelectorAll(".pick.sel").length < 3;
      if (btn && row.dataset.kind === "handle") { btn.hidden = state === "on"; btn.disabled = state === "busy"; btn.textContent = state === "busy" ? "Connecting…" : "Connect"; }
      if (ch) ch.hidden = state !== "on";
    }
    if (label !== undefined && label !== null) q("[data-sub]", row).textContent = label || row.dataset.what;
    var e = q("[data-err]", row); e.hidden = !err; e.textContent = err || "";
  }
  document.querySelectorAll(".src").forEach(function (row) {
    var name = row.dataset.source, kind = row.dataset.kind;
    q(".switch", row).addEventListener("click", async function () {
      var state = row.dataset.state;
      if (state === "busy") return;
      if (state === "pending") { setRow(row, "off", row.dataset.what); return; }
      if (state === "on") {
        setRow(row, "busy");
        var d = await post("/api/source/" + name, { on: false });
        if (d.ok) { setRow(row, "off", row.dataset.what); applyProfile(d.profile); } else { setRow(row, "on", null, d.error); }
        return;
      }
      if (kind === "oauth") { window.location = row.dataset.auth; return; }
      if (kind === "auto") {
        setRow(row, "busy");
        var r = await post("/api/source/" + name, { on: true });
        if (r.ok) { window.location = "/connect"; } else { setRow(row, "off", null, r.error); }
        return;
      }
      setRow(row, "pending", row.dataset.what);
      var inp = q("input", row); if (inp) inp.focus();
    });
    var form = q("[data-form]", row);
    if (form && kind === "shots") {
      form.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        var inp = q("input[type=file]", form);
        if (!inp.files.length) return;
        var fd = new FormData();
        Array.prototype.slice.call(inp.files, 0, 4).forEach(function (f) { fd.append("files", f, f.name || "screenshot.png"); });
        setRow(row, "busy");
        var d;
        try { var r = await fetch("/api/screenshot", { method: "POST", body: fd }); d = await r.json(); }
        catch (e) { d = { ok: false, error: "Network error. Try again." }; }
        if (d.ok) { setRow(row, "on", d.source ? d.source.label : "Saved"); applyProfile(d.profile); inp.value = ""; }
        else { setRow(row, "pending", row.dataset.what, d.error); }
      });
    }
    if (form && kind === "picks") {
      var picked = new Set(Array.from(form.querySelectorAll(".pick.sel")).map(function (b) { return b.dataset.vid; }));
      var save = q("[data-pick-save]", form), count = q("[data-pick-count]", form);
      function syncPicks() { count.textContent = picked.size + " picked"; save.disabled = picked.size < 3; }
      form.querySelectorAll(".pick").forEach(function (b) {
        b.addEventListener("click", function () {
          var v = b.dataset.vid;
          if (picked.has(v)) picked.delete(v); else picked.add(v);
          b.classList.toggle("sel", picked.has(v)); b.setAttribute("aria-pressed", picked.has(v) ? "true" : "false");
          syncPicks();
        });
      });
      save.addEventListener("click", async function () {
        setRow(row, "busy");
        var d = await post("/api/source/picks", { on: true, vids: Array.from(picked) });
        if (d.ok) { setRow(row, "on", d.source.label); applyProfile(d.profile); }
        else { setRow(row, "pending", row.dataset.what, d.error); }
      });
    }
    if (form && kind === "handle") {
      form.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        var handle = q("input[name=handle]", form).value.trim();
        if (!handle) return;
        setRow(row, "busy");
        var d = await post("/api/source/" + name, { on: true, handle: handle });
        if (d.ok) { setRow(row, "on", d.source.label); applyProfile(d.profile); }
        else { setRow(row, "pending", row.dataset.what, d.error); }
      });
      var ch = q("[data-change]", form);
      if (ch) ch.addEventListener("click", function (ev) { ev.preventDefault(); setRow(row, "pending", row.dataset.what); q("input[name=handle]", form).focus(); });
    }
  });
  var found = q("#found");
  if (found) {
    found.querySelectorAll("[data-lookup]").forEach(function (b) {
      b.addEventListener("click", async function () {
        var use = b.dataset.lookup === "yes";
        found.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
        var d = await post("/api/email-lookup", { use: use });
        if (!d.ok) { toast(d.error || "Something went wrong."); found.querySelectorAll("button").forEach(function (x) { x.disabled = false; }); return; }
        if (use && d.connected && d.connected.length) { window.location = "/connect"; return; }
        found.remove(); applyProfile(d.profile);
      });
    });
  }
})();
