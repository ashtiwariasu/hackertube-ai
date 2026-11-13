(function () {
  var prompt = document.getElementById("prompt").textContent;
  var answer = document.getElementById("answer"), go = document.getElementById("go"), err = document.getElementById("err");
  var progress = document.getElementById("progress"), ptext = document.getElementById("progress-text"), results = document.getElementById("results");
  var EXAMPLE = "You work as a backend engineer, mostly Go and Rust, running services on Kubernetes. You ask most about distributed systems, consensus and Raft, database internals, performance profiling, Rust async, compilers, and systems design interviews. Lately you've been learning how LLMs work under the hood: transformers, tokenization and fine-tuning. You've mentioned liking Andrej Karpathy, 3Blue1Brown, Computerphile, Strange Loop talks, Martin Kleppmann and Bret Victor's Inventing on Principle. You prefer long, deep lectures over short explainers, at an intermediate to advanced level. You'd probably enjoy talks on the history of computing, formal verification, and the design of programming languages.";
  function toast(m) { if (window.htToast) window.htToast(m); }
  function copy(then) {
    var done = function () { toast("Prompt copied. Paste it into your AI."); if (then) then(); };
    if (navigator.clipboard) navigator.clipboard.writeText(prompt).then(done, function () { if (then) then(); });
    else if (then) then();
  }
  document.getElementById("copy").addEventListener("click", function () { copy(); });
  document.querySelectorAll("[data-copy-first]").forEach(function (a) {
    a.addEventListener("click", function () { copy(); });
  });
  function sync() { go.disabled = answer.value.trim().length < 60; err.hidden = true; }
  answer.addEventListener("input", sync);
  document.getElementById("example").addEventListener("click", function () { answer.value = EXAMPLE; sync(); answer.focus(); });
  var steps = ["Reading your profile…", "Matching creators you like…", "Finding talks for what you're learning…"], timer = null;
  function busy(on) {
    go.disabled = on || answer.value.trim().length < 60; progress.hidden = !on; clearInterval(timer);
    if (on) { var i = 0; ptext.textContent = steps[0]; timer = setInterval(function () { i = (i + 1) % steps.length; ptext.textContent = steps[i]; }, 1600); }
  }
  go.addEventListener("click", async function () {
    busy(true); results.innerHTML = "";
    var d;
    try {
      var r = await fetch("/api/ai-profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: answer.value }) });
      d = await r.json();
    } catch (e) { d = { ok: false, error: "Network error. Try again." }; }
    busy(false);
    if (!d.ok) { err.textContent = d.error || "Something went wrong."; err.hidden = false; return; }
    results.innerHTML = d.html;
    if (window.htPaint) window.htPaint();
    if (!window.HT || !window.HT.loggedIn) { try { localStorage.setItem("ht_ai", JSON.stringify(d.store)); } catch (e) {} }
    else toast("Saved to your profile.");
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  });
})();
