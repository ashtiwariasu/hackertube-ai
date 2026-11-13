(function () {
  var drop = document.getElementById("drop"), input = document.getElementById("file"), previews = document.getElementById("previews");
  var go = document.getElementById("go"), sample = document.getElementById("sample"), err = document.getElementById("err");
  var progress = document.getElementById("progress"), ptext = document.getElementById("progress-text"), results = document.getElementById("results");
  var files = [];
  function showErr(m) { err.textContent = m || ""; err.hidden = !m; }
  function render() {
    previews.innerHTML = "";
    files.forEach(function (f, i) {
      var d = document.createElement("div"); d.className = "preview";
      var img = document.createElement("img"); img.alt = ""; img.src = URL.createObjectURL(f); d.appendChild(img);
      var x = document.createElement("button"); x.type = "button"; x.className = "preview-x"; x.setAttribute("aria-label", "Remove"); x.textContent = "×";
      x.addEventListener("click", function () { files.splice(i, 1); render(); }); d.appendChild(x);
      previews.appendChild(d);
    });
    go.disabled = files.length === 0;
    go.textContent = files.length > 1 ? "Read my " + files.length + " screenshots" : "Read my screenshot";
  }
  function add(list) {
    showErr("");
    Array.prototype.slice.call(list).forEach(function (f) {
      if (!/^image\/(png|jpeg|webp)$/.test(f.type)) { showErr("Use PNG, JPG or WebP screenshots."); return; }
      if (f.size > 8 * 1024 * 1024) { showErr("Each image needs to be under 8 MB."); return; }
      if (files.length < 4) files.push(f);
    });
    render();
  }
  document.getElementById("choose").addEventListener("click", function () { input.click(); });
  drop.addEventListener("click", function (e) { if (e.target === drop || e.target.classList.contains("drop-main")) input.click(); });
  drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", function () { add(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files) add(e.dataTransfer.files); });
  document.addEventListener("paste", function (e) {
    var items = (e.clipboardData || {}).items || [], got = [];
    for (var i = 0; i < items.length; i++) if (items[i].kind === "file") got.push(items[i].getAsFile());
    if (got.length) { add(got); drop.scrollIntoView({ behavior: "smooth", block: "center" }); }
  });
  var steps = ["Reading the titles…", "Matching against the whole catalogue…", "Working out your taste…", "Choosing what you'd like next…"];
  var timer = null;
  function busy(on) {
    go.disabled = on || files.length === 0; sample.disabled = on; progress.hidden = !on;
    clearInterval(timer);
    if (on) { var i = 0; ptext.textContent = steps[0]; timer = setInterval(function () { i = (i + 1) % steps.length; ptext.textContent = steps[i]; }, 1800); }
  }
  async function submit(fd) {
    showErr(""); busy(true); results.innerHTML = "";
    var d;
    try { var r = await fetch("/api/screenshot", { method: "POST", body: fd }); d = await r.json(); }
    catch (e) { d = { ok: false, error: "Network error. Try again." }; }
    busy(false);
    if (!d.ok) { showErr(d.error || "Something went wrong."); return; }
    results.innerHTML = d.html;
    if (window.htPaint) window.htPaint();
    if (!window.HT || !window.HT.loggedIn) {
      try { localStorage.setItem("ht_shots", JSON.stringify(d.store)); } catch (e) {}
    } else if (window.htToast) { window.htToast("Saved to your profile."); }
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  go.addEventListener("click", function () { var fd = new FormData(); files.forEach(function (f) { fd.append("files", f, f.name || "screenshot.png"); }); submit(fd); });
  sample.addEventListener("click", function () { var fd = new FormData(); fd.append("sample", "1"); submit(fd); });
})();
