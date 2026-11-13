(function () {
  var q = function (s, el) { return (el || document).querySelector(s); };
  var toast = function (m) { if (window.htToast) window.htToast(m); };
  var KEY = "ht_profile", INTO = "ht_into";
  function load(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  // ---- chips -> feed ----
  var chips = Array.prototype.slice.call(document.querySelectorAll(".chip-btn"));
  var feed = q("#feed"), why = q("#scope-why");
  var preset = chips.filter(function (c) { return c.classList.contains("on"); }).map(function (c) { return c.dataset.area; });
  var selected = preset.length ? preset : load(INTO, []);
  function paintChips() { chips.forEach(function (c) { c.classList.toggle("on", selected.indexOf(c.dataset.area) >= 0); }); }
  var feedReq = 0;
  async function refreshFeed(scrollTo) {
    var id = ++feedReq;
    try {
      var r = await fetch("/api/feed?areas=" + encodeURIComponent(selected.join(",")));
      if (!r.ok || id !== feedReq) return;
      feed.innerHTML = await r.text();
      if (window.htPaint) window.htPaint();
      if (why) why.textContent = selected.length ? "" : why.textContent;
      if (scrollTo) q("#chips").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {}
  }
  chips.forEach(function (c) {
    c.addEventListener("click", function () {
      var a = c.dataset.area, i = selected.indexOf(a);
      if (i >= 0) selected.splice(i, 1); else selected.push(a);
      if (selected.length > 4) selected.shift();
      save(INTO, selected); paintChips(); refreshFeed(false);
      if (window.htTrack) htTrack("chip_tap", { area: a, on: selected.indexOf(a) >= 0, n: selected.length });
      try { navigator.sendBeacon && navigator.sendBeacon("/api/ping", JSON.stringify({ ev: "chip", a: a })); } catch (e) {}
    });
  });
  if (!preset.length && selected.length) { paintChips(); refreshFeed(false); }

  // ---- paste anything ----
  var text = q("#anytext"), go = q("#anygo"), err = q("#anyerr"), prog = q("#anyprog"), ptext = q("#anyprogtext");
  var results = q("#anyresults"), input = q("#anyinput"), prev = q("#anyprev"), form = q("#anybox");
  var files = [], profile = load(KEY, { items: [] });
  function signalItems() {
    var w = load("ht_watch", {}), seen = load("ht_seen", []), hidden = load("ht_hidden", []), items = [];
    var done = Object.keys(w).filter(function (v) { return w[v].pct >= 0.6 && w[v].secs >= Math.min(0.3 * (w[v].dur || 0), 900); });
    var left = Object.keys(w).filter(function (v) { return w[v].pct < 0.2 && w[v].secs >= 30; });
    if (done.length || left.length) items.push({ kind: "watch", label: "Watched here: " + done.length + " video" + (done.length === 1 ? "" : "s"), vids: done, watched: done, texts: [], neg: left });
    if (seen.length || hidden.length) items.push({ kind: "feedback", label: (seen.length ? seen.length + " seen" : "") + (seen.length && hidden.length ? ", " : "") + (hidden.length ? hidden.length + " hidden" : ""), vids: seen, watched: seen, texts: [], neg: hidden });
    return items;
  }
  function hasPositive() { return signalItems().some(function (i) { return i.vids.length > 0; }); }
  function withSignals(p) { return { items: (p.items || []).concat(signalItems()) }; }
  function showErr(m) { err.textContent = m || ""; err.hidden = !m; }
  function sync() { go.disabled = !(text.value.trim().length >= 2 || files.length); }
  function renderPrev() {
    prev.innerHTML = "";
    files.forEach(function (f, i) {
      var d = document.createElement("span"); d.className = "preview";
      var img = document.createElement("img"); img.alt = ""; img.src = URL.createObjectURL(f); d.appendChild(img);
      var x = document.createElement("button"); x.type = "button"; x.className = "preview-x"; x.textContent = "×"; x.setAttribute("aria-label", "Remove");
      x.addEventListener("click", function () { files.splice(i, 1); renderPrev(); sync(); }); d.appendChild(x);
      prev.appendChild(d);
    });
  }
  function addFiles(list) {
    Array.prototype.slice.call(list).forEach(function (f) {
      if (!/^image\/(png|jpeg|webp)$/.test(f.type)) { showErr("Images need to be PNG, JPG or WebP."); return; }
      if (f.size > 8 * 1024 * 1024) { showErr("Each image needs to be under 8 MB."); return; }
      if (files.length < 4) files.push(f);
    });
    renderPrev(); sync();
  }
  q("#anyfile").addEventListener("click", function () { input.click(); });
  input.addEventListener("change", function () { addFiles(input.files); input.value = ""; });
  text.addEventListener("input", sync);
  text.addEventListener("paste", function (e) {
    var items = (e.clipboardData || {}).items || [], got = [];
    for (var i = 0; i < items.length; i++) if (items[i].kind === "file") got.push(items[i].getAsFile());
    if (got.length) { e.preventDefault(); addFiles(got); }
  });
  ["dragenter", "dragover"].forEach(function (ev) { form.addEventListener(ev, function (e) { e.preventDefault(); form.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { form.addEventListener(ev, function (e) { e.preventDefault(); form.classList.remove("over"); }); });
  form.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
  var steps = ["Reading that…", "Matching against the whole catalogue…", "Working out your taste…", "Writing the reasons…"], timer = null;
  function busy(on) {
    go.disabled = on; prog.hidden = !on; clearInterval(timer);
    if (on) { var i = 0; ptext.textContent = steps[0]; timer = setInterval(function () { i = (i + 1) % steps.length; ptext.textContent = steps[i]; }, 1700); }
  }
  async function submit() {
    showErr(""); busy(true);
    if (window.htTrack) htTrack("paste_submitted", { chars: text.value.trim().length, images: files.length, has_link: /https?:\/\//.test(text.value) });
    var fd = new FormData();
    fd.append("text", text.value); fd.append("prior", JSON.stringify(withSignals(profile)));
    files.forEach(function (f) { fd.append("files", f, f.name || "screenshot.png"); });
    var d;
    try { var r = await fetch("/api/anything", { method: "POST", body: fd }); d = await r.json(); }
    catch (e) { d = { ok: false, error: "Network error. Try again." }; }
    busy(false);
    if (!d.ok) { showErr(d.error || "Something went wrong."); sync(); if (window.htTrack) htTrack("paste_failed", { error: (d.error || "").slice(0, 60) }); return; }
    profile = d.store; save(KEY, profile);
    if (window.htTrack) htTrack("picks_shown", { chips: (profile.items || []).length, source: "paste" });
    results.innerHTML = d.html;
    if (window.htPaint) window.htPaint();
    text.value = ""; files = []; renderPrev(); sync();
    if (window.HT && window.HT.loggedIn) toast("Saved to your profile.");
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  form.addEventListener("submit", function (e) { e.preventDefault(); if (!go.disabled) submit(); });
  results.addEventListener("click", function (e) {
    var x = e.target.closest(".chip-x"); if (!x) return;
    var i = parseInt(x.parentElement.dataset.i, 10);
    profile.items.splice(i, 1); save(KEY, profile);
    if (!profile.items.length) { results.innerHTML = ""; return; }
    busy(true);
    var fd = new FormData(); fd.append("text", ""); fd.append("prior", JSON.stringify(withSignals(profile)));
    fetch("/api/anything", { method: "POST", body: fd }).then(function (r) { return r.json(); }).then(function (d) {
      busy(false); if (d.ok) { profile = d.store; save(KEY, profile); results.innerHTML = d.html; if (window.htPaint) window.htPaint(); }
    }).catch(function () { busy(false); });
  });
  // "Because you opened X": the row closest to the last video this browser opened
  var opened = load("ht_opened", []);
  if (opened.length) {
    fetch("/api/because", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ opened: opened, hidden: load("ht_hidden", []), seen: load("ht_seen", []) }) }).then(function (r) { return r.text(); }).then(function (h) {
      var b = q("#because"); if (b && h) { b.innerHTML = h; if (window.htPaint) window.htPaint(); if (window.htTrack) htTrack("because_shown", { anchor: opened[0], opened: opened.length }); }
    }).catch(function () {});
  }
  // anonymous continue-watching row from the browser's own watch map
  var w0 = load("ht_watch", {});
  if (Object.keys(w0).length && !(window.HT && window.HT.loggedIn)) {
    fetch("/api/continue", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ watch: w0, hidden: load("ht_hidden", []), seen: load("ht_seen", []) }) }).then(function (r) { return r.text(); }).then(function (h) {
      var c = q("#continue"); if (c && h) { c.innerHTML = h; if (window.htPaint) window.htPaint(); }
    }).catch(function () {});
  }
  // returning visitor with a saved profile or watch signals: show it without asking
  if ((profile.items && profile.items.length || hasPositive()) && !(window.HT && window.HT.loggedIn)) {
    var fd0 = new FormData(); fd0.append("text", ""); fd0.append("prior", JSON.stringify(withSignals(profile)));
    fetch("/api/anything", { method: "POST", body: fd0 }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok) { results.innerHTML = d.html; if (window.htPaint) window.htPaint(); }
    }).catch(function () {});
  }
})();
